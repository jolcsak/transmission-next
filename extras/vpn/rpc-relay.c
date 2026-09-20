// SPDX-License-Identifier: GPL-2.0-or-later
// Bounded, unprivileged loopback TCP relay. No parsing, DNS, threads or forks.
#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

enum { MAX_CONNECTIONS = 16, BUFFER_SIZE = 16384 };
struct buffer { size_t begin, end; unsigned char data[BUFFER_SIZE]; };
struct connection {
    int fd[2];
    bool connecting, eof[2], shut[2];
    struct buffer outbound[2]; // bytes read from side i, awaiting write to side 1-i
    int64_t active, started;
};
static volatile sig_atomic_t stopping;
static void stop(int signal_number) { (void)signal_number; stopping = 1; }
static int64_t now_ms(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) abort();
    return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}
static int port_number(char const* text) {
    char* end;
    long value = strtol(text, &end, 10);
    return *text && !*end && value >= 1024 && value <= 65535 ? (int)value : -1;
}
static int nonblocking(int fd) {
    int flags = fcntl(fd, F_GETFL);
    return flags == -1 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) == -1 ||
        fcntl(fd, F_SETFD, FD_CLOEXEC) == -1 ? -1 : 0;
}
static void close_connection(struct connection** slot) {
    if (!*slot) return;
    close((*slot)->fd[0]);
    close((*slot)->fd[1]);
    free(*slot);
    *slot = NULL;
}
static short interests(struct connection const* c, int side) {
    if (c->connecting) return side == 1 ? POLLOUT : 0;
    short events = 0;
    if (!c->eof[side] && c->outbound[side].end < BUFFER_SIZE) events |= POLLIN;
    struct buffer const* incoming = &c->outbound[1-side];
    if (incoming->begin < incoming->end) events |= POLLOUT;
    return events;
}
static bool transfer(struct connection* c, int side, short events, int64_t now) {
    if (events & (POLLERR | POLLNVAL)) return false;
    struct buffer* sendbuf = &c->outbound[1-side];
    if ((events & (POLLOUT | POLLHUP)) && sendbuf->begin < sendbuf->end) {
        ssize_t n = send(c->fd[side], sendbuf->data + sendbuf->begin,
                         sendbuf->end - sendbuf->begin, MSG_NOSIGNAL);
        if (n > 0) {
            sendbuf->begin += (size_t)n;
            if (sendbuf->begin == sendbuf->end) sendbuf->begin = sendbuf->end = 0;
            c->active = now;
        } else if (n == 0 || (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)) return false;
    }
    struct buffer* recvbuf = &c->outbound[side];
    if ((events & (POLLIN | POLLHUP)) && !c->eof[side] && recvbuf->end < BUFFER_SIZE) {
        ssize_t n = recv(c->fd[side], recvbuf->data + recvbuf->end, BUFFER_SIZE - recvbuf->end, 0);
        if (n > 0) { recvbuf->end += (size_t)n; c->active = now; }
        else if (n == 0) c->eof[side] = true;
        else if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) return false;
    }
    return true;
}
int main(int argc, char** argv) {
    struct sockaddr_in target = {.sin_family = AF_INET};
    if (argc != 4 || port_number(argv[1]) < 0 || port_number(argv[3]) < 0 ||
        inet_pton(AF_INET, argv[2], &target.sin_addr) != 1) {
        fputs("usage: rpc-relay LOCAL_PORT TARGET_IPV4 TARGET_PORT\n", stderr);
        return 2;
    }
    target.sin_port = htons((uint16_t)port_number(argv[3]));
    struct sigaction action = {.sa_handler = stop};
    sigemptyset(&action.sa_mask);
    sigaction(SIGTERM, &action, NULL);
    sigaction(SIGINT, &action, NULL);
    signal(SIGPIPE, SIG_IGN);
    int listener = socket(AF_INET, SOCK_STREAM, 0);
    if (listener == -1 || nonblocking(listener) == -1) { perror("socket"); return 1; }
    int yes = 1;
    setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    struct sockaddr_in local = {.sin_family = AF_INET, .sin_addr.s_addr = htonl(INADDR_LOOPBACK),
                                .sin_port = htons((uint16_t)port_number(argv[1]))};
    if (bind(listener, (struct sockaddr*)&local, sizeof(local)) != 0 || listen(listener, 16) != 0) {
        perror("listen"); close(listener); return 1;
    }
    struct connection* slots[MAX_CONNECTIONS] = {0};
    int result = 0;
    while (!stopping) {
        struct pollfd fds[1 + 2*MAX_CONNECTIONS];
        memset(fds, 0, sizeof(fds));
        fds[0].fd = listener; fds[0].events = POLLIN;
        int timeout = -1;
        int64_t now = now_ms();
        for (int i = 0; i < MAX_CONNECTIONS; ++i) {
            struct connection* c = slots[i];
            for (int side = 0; side < 2; ++side) {
                struct pollfd* p = &fds[1+2*i+side];
                p->events = c ? interests(c, side) : 0;
                p->fd = p->events ? c->fd[side] : -1;
            }
            if (c) {
                int64_t remaining = (c->connecting ? c->started + 5000 : c->active + 120000) - now;
                int wait = remaining <= 0 ? 0 : (int)remaining;
                if (timeout == -1 || wait < timeout) timeout = wait;
            }
        }
        int ready = poll(fds, 1+2*MAX_CONNECTIONS, timeout);
        if (ready < 0) { if (errno == EINTR) continue; perror("poll"); result = 1; break; }
        now = now_ms();
        // Process only connections represented in this poll snapshot, before accepting new ones.
        for (int i = 0; i < MAX_CONNECTIONS; ++i) {
            struct connection* c = slots[i];
            if (!c) continue;
            bool good = now < (c->connecting ? c->started + 5000 : c->active + 120000);
            if (good && c->connecting && fds[2+2*i].revents) {
                int error = 0; socklen_t length = sizeof(error);
                good = getsockopt(c->fd[1], SOL_SOCKET, SO_ERROR, &error, &length) == 0 && error == 0;
                if (good) { c->connecting = false; c->active = now; }
            }
            if (good && !c->connecting) {
                for (int side = 0; good && side < 2; ++side)
                    good = transfer(c, side, fds[1+2*i+side].revents, now);
                for (int side = 0; good && side < 2; ++side) {
                    if (c->eof[side] && c->outbound[side].end == 0 && !c->shut[1-side]) {
                        shutdown(c->fd[1-side], SHUT_WR);
                        c->shut[1-side] = true;
                    }
                }
                if (c->eof[0] && c->eof[1] && !c->outbound[0].end && !c->outbound[1].end) good = false;
            }
            if (!good) close_connection(&slots[i]);
        }
        if (fds[0].revents & POLLIN) {
            // Limit accept work per iteration; existing streams must keep progressing.
            for (int count = 0; count < MAX_CONNECTIONS; ++count) {
                int client = accept(listener, NULL, NULL);
                if (client == -1) break;
                int slot = 0;
                while (slot < MAX_CONNECTIONS && slots[slot]) ++slot;
                if (slot == MAX_CONNECTIONS || nonblocking(client) != 0) { close(client); continue; }
                int upstream = socket(AF_INET, SOCK_STREAM, 0);
                if (upstream == -1) { close(client); continue; }
                if (nonblocking(upstream) != 0) { close(client); close(upstream); continue; }
                int connected = connect(upstream, (struct sockaddr*)&target, sizeof(target));
                if (connected != 0 && errno != EINPROGRESS) { close(client); close(upstream); continue; }
                struct connection* c = calloc(1, sizeof(*c));
                if (!c) { close(client); close(upstream); continue; }
                c->fd[0] = client; c->fd[1] = upstream;
                c->connecting = connected != 0;
                c->active = c->started = now;
                slots[slot] = c;
            }
        }
    }
    for (int i = 0; i < MAX_CONNECTIONS; ++i) close_connection(&slots[i]);
    close(listener);
    return result;
}
