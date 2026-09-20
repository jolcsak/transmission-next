// This file Copyright © Mnemosyne LLC.
// SPDX-License-Identifier: GPL-2.0-or-later

#include <chrono>
#include <future>
#include <string>
#include <thread>

#include <event2/util.h>

#include <libtransmission/peer-io.h>
#include <libtransmission/peer-socket.h>
#include <libtransmission/peer-socket-tcp.h>
#include <libtransmission/tr-buffer.h>

#include "test-fixtures.h"

namespace tr::test
{
// A bounded transport makes partial writes deterministic without timing races.
class BoundedSocket final : public tr_peer_socket
{
public:
    BoundedSocket()
        : tr_peer_socket{ { *tr_address::from_string("127.0.0.1"), tr_port::from_host(8080) } }
    {
    }
    void set_read_enabled(bool enabled) override
    {
        read_enabled = enabled;
    }
    void set_write_enabled(bool enabled) override
    {
        write_enabled = enabled;
    }
    bool is_read_enabled() const override
    {
        return read_enabled;
    }
    bool is_write_enabled() const override
    {
        return write_enabled;
    }
    std::string written;
    size_t limit = 7;
    bool capture = true;
    size_t reads = 0;
    std::string input = "*";

protected:
    Type type() const noexcept override
    {
        return Type::TCP;
    }
    size_t try_read_impl(InBuf& buf, size_t, tr_error*) override
    {
        ++reads;
        buf.add(input);
        return input.size();
    }
    size_t try_write_impl(OutBuf& buf, size_t n, tr_error*) override
    {
        n = std::min(n, limit);
        if (capture)
        {
            written.append(reinterpret_cast<char const*>(buf.data()), n);
        }
        buf.drain(n);
        return n;
    }

private:
    bool read_enabled = false;
    bool write_enabled = false;
};

class NetworkOptimizationTest : public SessionTest
{
protected:
    void inSession(std::function<void()> const& fn)
    {
        auto done = std::promise<void>{};
        auto future = done.get_future();
        session_->run_in_session_thread(
            [&]()
            {
                fn();
                done.set_value();
            });
        future.get();
    }
};

TEST_F(NetworkOptimizationTest, PlaintextViewPreservesUnreadAndEncryptedInput)
{
    inSession(
        [&]()
        {
            auto socket = std::make_shared<BoundedSocket>();
            socket->input = "abcdefgh";
            auto io = tr_peerIo::new_incoming(session_, &session_->top_bandwidth_, socket);
            io->set_callbacks(
                [](tr_peerIo* peer, void*, size_t*)
                {
                    EXPECT_FALSE(peer->read_plaintext_view(9));
                    EXPECT_EQ(8U, peer->read_buffer_size());
                    auto first = peer->read_plaintext_view(3);
                    EXPECT_TRUE(first);
                    if (first)
                    {
                        EXPECT_EQ("abc", std::string(reinterpret_cast<char const*>(first->data()), first->size()));
                    }
                    auto middle = std::array<char, 2>{};
                    peer->read_bytes(middle.data(), middle.size());
                    EXPECT_EQ("de", std::string(middle.data(), middle.size()));
                    auto last = peer->read_plaintext_view(3);
                    EXPECT_TRUE(last);
                    if (last)
                    {
                        EXPECT_EQ("fgh", std::string(reinterpret_cast<char const*>(last->data()), last->size()));
                    }
                    EXPECT_EQ(0U, peer->read_buffer_size());
                    return ReadState::Later;
                },
                nullptr,
                nullptr,
                nullptr);
            EXPECT_EQ(8U, io->flush(tr_direction::Down, 1024));

            using namespace tr_message_stream_encryption;
            using MseDH = tr_message_stream_encryption::DH;
            auto dh = MseDH{ MseDH::private_key_bigend_t{} };
            auto hash = tr_sha1_digest_t{};
            auto encoder = Filter{};
            encoder.encrypt_init(false, dh, hash);
            encoder.encrypt(socket->input.data(), socket->input.size(), socket->input.data());
            io->decrypt_init(true, dh, hash);
            io->set_callbacks(
                [](tr_peerIo* peer, void*, size_t*)
                {
                    EXPECT_FALSE(peer->read_plaintext_view(8));
                    EXPECT_EQ(8U, peer->read_buffer_size());
                    auto plain = std::array<char, 8>{};
                    peer->read_bytes(plain.data(), plain.size());
                    EXPECT_EQ("abcdefgh", std::string(plain.data(), plain.size()));
                    return ReadState::Later;
                },
                nullptr,
                nullptr,
                nullptr);
            EXPECT_EQ(8U, io->flush(tr_direction::Down, 1024));
            io->clear();
        });
}

#ifndef _WIN32
TEST_F(NetworkOptimizationTest, TcpWritePollingStopsWhenIdleAndResumes)
{
    evutil_socket_t fds[2];
    ASSERT_EQ(0, evutil_socketpair(AF_UNIX, SOCK_STREAM, 0, fds));
    evutil_make_socket_nonblocking(fds[0]);
    evutil_make_socket_nonblocking(fds[1]);
    std::shared_ptr<tr_peer_socket> socket;
    std::shared_ptr<tr_peerIo> io;
    size_t callbacks = 0;
    size_t accounted = 0;
    auto complete = std::promise<void>{};
    auto ready = complete.get_future();
    inSession([&]()
    {
        socket = tr_peer_socket_tcp::create(
            *session_, { *tr_address::from_string("127.0.0.1"), tr_port::from_host(8080) }, fds[0]);
        io = tr_peerIo::new_incoming(session_, &session_->top_bandwidth_, socket);
        io->set_callbacks(nullptr,
            [](tr_peerIo*, size_t n, bool, void* data) { *static_cast<size_t*>(data) += n; }, nullptr, &accounted);
        socket->set_write_cb([&]()
        {
            EXPECT_TRUE(socket->is_write_enabled());
            ++callbacks;
            io->flush(tr_direction::Up, 7);
            if (!socket->is_write_enabled())
            {
                complete.set_value();
            }
        });
        io->write_bytes("abcdefghijklmnopqrstuvwx", 24, true);
        io->set_enabled(tr_direction::Up, true);
    });
    EXPECT_EQ(std::future_status::ready, ready.wait_for(std::chrono::seconds{ 3 }));
    std::this_thread::sleep_for(std::chrono::milliseconds{ 20 });
    inSession([&]()
    {
        EXPECT_EQ(4U, callbacks);
        EXPECT_EQ(24U, accounted);
        EXPECT_FALSE(socket->is_write_enabled());
        auto received = std::array<char, 64>{};
        auto const n = recv(fds[1], received.data(), received.size(), 0);
        ASSERT_EQ(24, n);
        EXPECT_EQ("abcdefghijklmnopqrstuvwx", std::string(received.data(), n));
        complete = std::promise<void>{};
        ready = complete.get_future();
        io->write_bytes("resume", 6, true);
        io->set_enabled(tr_direction::Up, true);
    });
    EXPECT_EQ(std::future_status::ready, ready.wait_for(std::chrono::seconds{ 3 }));
    inSession([&]()
    {
        EXPECT_EQ(5U, callbacks);
        EXPECT_EQ(30U, accounted);
        EXPECT_FALSE(socket->is_write_enabled());
        auto received = std::array<char, 64>{};
        auto const n = recv(fds[1], received.data(), received.size(), 0);
        EXPECT_EQ(6, n);
        if (n == 6)
        {
            EXPECT_EQ("resume", std::string(received.data(), n));
        }
        // An exhausted bandwidth budget must also disable a writable socket.
        io->bandwidth().set_limited(tr_direction::Up, true);
        io->bandwidth().set_desired_speed(tr_direction::Up, {});
        io->write_bytes("blocked", 7, true);
        io->set_enabled(tr_direction::Up, true);
        EXPECT_EQ(0U, io->flush(tr_direction::Up, 7));
        EXPECT_FALSE(socket->is_write_enabled());
        io->clear();
        io.reset();
        socket.reset();
    });
    evutil_closesocket(fds[1]);
}
#endif

TEST_F(NetworkOptimizationTest, PartialWritesPreserveBytesAndAccounting)
{
    inSession(
        [&]()
        {
            auto socket = std::make_shared<BoundedSocket>();
            auto io = tr_peerIo::new_incoming(session_, &session_->top_bandwidth_, socket);
            std::string classes;
            io->set_callbacks(
                nullptr,
                [](tr_peerIo*, size_t n, bool piece, void* user)
                { static_cast<std::string*>(user)->append(n, piece ? 'd' : 'p'); },
                nullptr,
                &classes);
            io->write_bytes("abcd", 4, false);
            io->write_bytes("efgh", 4, false);
            io->write_bytes("ijklmnop", 8, true);
            io->write_bytes("qrst", 4, true);
            io->write_bytes("uvwx", 4, false);
            io->write_bytes(nullptr, 0, true);
            EXPECT_EQ(7U, io->flush(tr_direction::Up, 100));
            EXPECT_EQ(1U, io->flush_outgoing_protocol_msgs());
            EXPECT_EQ(0U, io->flush_outgoing_protocol_msgs());
            EXPECT_EQ(0U, io->flush(tr_direction::Up, 0));
            EXPECT_EQ(3U, io->flush(tr_direction::Up, 3));
            while (io->flush(tr_direction::Up, 100) != 0U)
            {
            }
            EXPECT_EQ("abcdefghijklmnopqrstuvwx", socket->written);
            EXPECT_EQ(std::string(8, 'p') + std::string(12, 'd') + std::string(4, 'p'), classes);
            io->write_bytes("discard", 7, true);
            io->clear();
            EXPECT_EQ(0U, io->flush(tr_direction::Up, 100));
        });
}

TEST_F(NetworkOptimizationTest, CompactionPreservesUnreadTail)
{
    auto buf = tr::StackBuffer<32, char>{};
    auto expected = std::string{};
    for (size_t round = 0; round < 2000; ++round)
    {
        auto data = std::string(33 + round % 211, static_cast<char>('a' + round % 26));
        buf.add(data);
        expected += data;
        EXPECT_EQ(expected, std::string(buf.data(), buf.size()));
        auto n = expected.size() * 3 / 4;
        buf.drain(n);
        expected.erase(0, n);
    }
}

TEST_F(NetworkOptimizationTest, FatalReadStopsTransportUntilSocketReplacement)
{
    inSession(
        [&]()
        {
            auto socket = std::make_shared<BoundedSocket>();
            auto io = tr_peerIo::new_incoming(session_, &session_->top_bandwidth_, socket);
            io->set_callbacks([](tr_peerIo*, void*, size_t*) { return ReadState::Err; }, nullptr, nullptr, nullptr);
            EXPECT_EQ(1U, io->flush(tr_direction::Down, 1024));
            EXPECT_FALSE(socket->is_read_enabled());
            io->set_enabled(tr_direction::Down, true);
            EXPECT_FALSE(socket->is_read_enabled());
            EXPECT_EQ(0U, io->flush(tr_direction::Down, 1024));
            EXPECT_EQ(1U, socket->reads);
            auto replacement = std::make_shared<BoundedSocket>();
            io->set_socket(replacement);
            io->set_callbacks(
                [](tr_peerIo* peer, void*, size_t*)
                {
                    peer->read_buffer_discard(peer->read_buffer_size());
                    return ReadState::Later;
                },
                nullptr,
                nullptr,
                nullptr);
            EXPECT_EQ(1U, io->flush(tr_direction::Down, 1024));
            EXPECT_EQ(1U, replacement->reads);
            EXPECT_EQ(0U, io->read_buffer_size());
            io->clear();
        });
}

TEST_F(NetworkOptimizationTest, DISABLED_BenchmarkWriteAccounting)
{
    inSession(
        [&]()
        {
            auto socket = std::make_shared<BoundedSocket>();
            socket->limit = 1024 * 1024;
            socket->capture = false;
            auto io = tr_peerIo::new_incoming(session_, &session_->top_bandwidth_, socket);
            size_t accounted = 0;
            io->set_callbacks(
                nullptr,
                [](tr_peerIo*, size_t n, bool, void* user) { *static_cast<size_t*>(user) += n; },
                nullptr,
                &accounted);
            for (int sample = 0; sample < 9; ++sample)
            {
                auto const start = std::chrono::steady_clock::now();
                for (int batch = 0; batch < 2000; ++batch)
                {
                    for (int i = 0; i < 1024; ++i)
                    {
                        io->write_bytes("hello", 5, false);
                    }
                    io->flush(tr_direction::Up, 1024 * 1024);
                }
                auto const ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
                fmt::print("NETWORK_BENCH accounting {} {:.6f}\n", sample, ms);
            }
            EXPECT_EQ(9U * 2000U * 1024U * 5U, accounted);
            io->clear();
        });
}
} // namespace tr::test
