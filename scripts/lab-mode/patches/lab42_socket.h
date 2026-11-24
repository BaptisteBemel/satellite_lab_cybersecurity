/* Linux TCP sender for the pinned NOS3 42 simulator. */
#ifndef LAB42_SOCKET_H
#define LAB42_SOCKET_H
#ifndef _POSIX_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#endif
#include <arpa/inet.h>
#include <errno.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>

static inline int64_t Lab42MonotonicMs(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return -1;
    return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

/* Preserve frame order and every byte, including after a partial send.
 * The fd stays nonblocking: the original optional ACK read must not block.
 * A slow receiver gets a bounded wait; permanent errors remain failures. */
static inline int Lab42SendAll(int fd, const void *data, size_t length, int timeout_ms)
{
    const unsigned char *bytes = (const unsigned char *)data;
    size_t offset = 0;
    int64_t now, deadline;
    if (timeout_ms <= 0) { errno = EINVAL; return -1; }
    now = Lab42MonotonicMs();
    if (now < 0) return -1;
    deadline = now + timeout_ms;
    while (offset < length) {
        ssize_t count;
        now = Lab42MonotonicMs();
        if (now < 0) return -1;
        if (now >= deadline) { errno = ETIMEDOUT; return -1; }
        count = send(fd, bytes + offset, length - offset, MSG_NOSIGNAL);
        if (count > 0) { offset += (size_t)count; continue; }
        if (count == 0) { errno = EPIPE; return -1; }
        if (errno == EINTR) continue;
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
            struct pollfd pending = {fd, POLLOUT, 0};
            int ready;
            now = Lab42MonotonicMs();
            if (now < 0) return -1;
            if (now >= deadline) { errno = ETIMEDOUT; return -1; }
            ready = poll(&pending, 1, (int)(deadline - now));
            if (ready < 0) { if (errno == EINTR) continue; return -1; }
            if (ready == 0) { errno = ETIMEDOUT; return -1; }
            if (pending.revents & POLLNVAL) { errno = EBADF; return -1; }
            if (pending.revents & (POLLERR | POLLHUP)) {
                int error = 0;
                socklen_t size = sizeof(error);
                if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &size) < 0) return -1;
                errno = error ? error : EPIPE;
                return -1;
            }
            continue;
        }
        return -1;
    }
    return 0;
}

static inline void Lab42ReportSendFailure(int fd)
{
    int error = errno;
    struct sockaddr_in local = {0}, peer = {0};
    socklen_t size = sizeof(local);
    (void)getsockname(fd, (struct sockaddr *)&local, &size);
    size = sizeof(peer);
    (void)getpeername(fd, (struct sockaddr *)&peer, &size);
    /* Flush the old per-frame blank lines before the final error diagnostic. */
    (void)fflush(stdout);
    fprintf(stderr, "LAB42_SEND_V1: fd=%d local_port=%u peer_port=%u errno=%d (%s)\n",
            fd, (unsigned)ntohs(local.sin_port), (unsigned)ntohs(peer.sin_port),
            error, strerror(error));
    errno = error;
}
#endif
