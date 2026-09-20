// SPDX-License-Identifier: GPL-2.0-or-later
#include <gtest/gtest.h>
#include <libtransmission/peer-message-limits.h>
#include <libtransmission/rpc-login-limiter.h>
#include <libtransmission/security-log.h>

TEST(SecurityLog, FloodIsBoundedAndSuppressionReported)
{
    tr_security_log_window window;
    auto const now = tr_security_log_window::Clock::time_point{};
    uint64_t suppressed = 0;
    size_t emitted = 0;
    for (int i = 0; i < 5000; ++i) emitted += window.admit(now, suppressed);
    EXPECT_EQ(5U, emitted);
    EXPECT_EQ(4995U, window.suppressed);
    EXPECT_TRUE(window.admit(now + std::chrono::seconds{60}, suppressed));
    EXPECT_EQ(4995U, suppressed);
    EXPECT_EQ(0U, window.suppressed);
}

TEST(PeerMsgs, RejectsOversizedExtensionAndMagnetBitfield)
{
    EXPECT_TRUE(tr_peer_message_length_valid(20, 16386, std::nullopt));
    EXPECT_TRUE(tr_peer_message_length_valid(20, 256U * 1024U, std::nullopt));
    EXPECT_FALSE(tr_peer_message_length_valid(20, 256U * 1024U + 1, std::nullopt));
    EXPECT_FALSE(tr_peer_message_length_valid(20, UINT32_MAX, 100));
    EXPECT_FALSE(tr_peer_message_length_valid(20, 1, 100));
    EXPECT_TRUE(tr_peer_message_length_valid(5, 1024U * 1024U, std::nullopt));
    EXPECT_FALSE(tr_peer_message_length_valid(5, 1024U * 1024U + 1, std::nullopt));
    EXPECT_FALSE(tr_peer_message_length_valid(5, UINT32_MAX, std::nullopt));
    EXPECT_TRUE(tr_peer_message_length_valid(5, 3, 9));
    EXPECT_FALSE(tr_peer_message_length_valid(5, 4, 9));
    EXPECT_TRUE(tr_peer_message_length_valid(5, 536870913, UINT32_MAX));
    EXPECT_FALSE(tr_peer_message_length_valid(7, 8, 100));
    EXPECT_TRUE(tr_peer_message_length_valid(7, 16393, 100));
    EXPECT_FALSE(tr_peer_message_length_valid(7, 16394, 100));
    EXPECT_FALSE(tr_peer_message_length_valid(99, 1, 100));
}

TEST(RpcLoginLimiter, ExpiresWithoutTrafficAndSeparatesSources)
{
    tr_rpc_login_limiter limiter;
    auto now = tr_rpc_login_limiter::Clock::time_point{};
    limiter.failed("a", 2, now);
    EXPECT_FALSE(limiter.blocked("a", now));
    limiter.failed("a", 2, now);
    EXPECT_TRUE(limiter.blocked("a", now));
    EXPECT_FALSE(limiter.blocked("b", now));
    EXPECT_TRUE(limiter.blocked("a", now + std::chrono::seconds{29}));
    EXPECT_FALSE(limiter.blocked("a", now + std::chrono::seconds{30}));
    limiter.failed("a", 2, now + std::chrono::seconds{31});
    limiter.success("a");
    limiter.failed("a", 2, now + std::chrono::seconds{32});
    EXPECT_FALSE(limiter.blocked("a", now + std::chrono::seconds{32}));
    limiter.clear();
    for (int i = 0; i < 1000; ++i) limiter.failed(std::to_string(i), 1, now);
    EXPECT_TRUE(limiter.blocked("999", now));
    EXPECT_FALSE(limiter.blocked("999", now + std::chrono::seconds{30}));
}
