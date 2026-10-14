/* Linux socket-denial launcher. Trusted harness boundary, NOT a full agent sandbox.
 * No libseccomp dependency. Supported native ABIs: x86_64 and aarch64.
 * Close inherited descriptors; reject socket stdio; install filter BEFORE exec.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/audit.h>
#include <linux/capability.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

#if defined(__x86_64__)
#define NATIVE_ARCH AUDIT_ARCH_X86_64
#elif defined(__aarch64__)
#define NATIVE_ARCH AUDIT_ARCH_AARCH64
#else
#error "Unsupported ABI: do not silently fall back to a Python network monkeypatch"
#endif
#define DENY(n) BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K, (n), 0, 1), BPF_STMT(BPF_RET|BPF_K, SECCOMP_RET_ERRNO | EPERM)

static void fail(const char *what) { perror(what); exit(78); }
static void close_inherited(void) {
    for (int fd=0; fd<3; ++fd) {
        struct stat st;
        if (fstat(fd, &st) == 0 && S_ISSOCK(st.st_mode)) {
            fprintf(stderr,"Refusing inherited socket on stdio %d\n",fd); exit(78);
        }
    }
#ifdef SYS_close_range
    if (syscall(SYS_close_range, 3U, ~0U, 0U) == 0) return;
#endif
    struct rlimit lim;
    if (getrlimit(RLIMIT_NOFILE, &lim)) fail("getrlimit");
    if (lim.rlim_max == RLIM_INFINITY || lim.rlim_max > 1048576) {
        fprintf(stderr,"Cannot establish bounded descriptor closure\n"); exit(78);
    }
    for (int fd=3; (rlim_t)fd<lim.rlim_max; ++fd) close(fd);
}
static void drop_capabilities(void) {
    /* No namespace or device-management capability should survive even under uid 0. */
    struct __user_cap_header_struct h = { _LINUX_CAPABILITY_VERSION_3, 0 };
    struct __user_cap_data_struct d[2] = {{0,0,0},{0,0,0}};
    if (syscall(SYS_capset, &h, d)) fail("capset");
}
static void install_filter(void) {
    struct sock_filter code[] = {
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS, offsetof(struct seccomp_data, arch)),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K, NATIVE_ARCH, 1, 0),
        BPF_STMT(BPF_RET|BPF_K, SECCOMP_RET_KILL_PROCESS),
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS, offsetof(struct seccomp_data, nr)),
#if defined(__x86_64__)
        /* Reject the x32 ABI rather than accidentally allowing alternate syscall numbers. */
        BPF_JUMP(BPF_JMP|BPF_JGE|BPF_K, 0x40000000U, 0, 1),
        BPF_STMT(BPF_RET|BPF_K, SECCOMP_RET_ERRNO | EPERM),
#endif
        DENY(SYS_socket), DENY(SYS_socketpair), DENY(SYS_connect),
        DENY(SYS_bind), DENY(SYS_listen), DENY(SYS_accept),
#ifdef SYS_accept4
        DENY(SYS_accept4),
#endif
        DENY(SYS_sendto), DENY(SYS_recvfrom), DENY(SYS_sendmsg), DENY(SYS_recvmsg),
#ifdef SYS_sendmmsg
        DENY(SYS_sendmmsg),
#endif
#ifdef SYS_recvmmsg
        DENY(SYS_recvmmsg),
#endif
#ifdef SYS_socketcall
        DENY(SYS_socketcall),
#endif
#ifdef SYS_io_uring_setup
        DENY(SYS_io_uring_setup), DENY(SYS_io_uring_enter), DENY(SYS_io_uring_register),
#endif
#ifdef SYS_pidfd_getfd
        DENY(SYS_pidfd_getfd),
#endif
        DENY(SYS_ptrace), DENY(SYS_setns), DENY(SYS_unshare),
#ifdef SYS_process_vm_readv
        DENY(SYS_process_vm_readv), DENY(SYS_process_vm_writev),
#endif
#ifdef SYS_bpf
        DENY(SYS_bpf),
#endif
        BPF_STMT(BPF_RET|BPF_K, SECCOMP_RET_ALLOW)
    };
    struct sock_fprog program = { (unsigned short)(sizeof(code)/sizeof(code[0])), code };
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) fail("PR_SET_NO_NEW_PRIVS");
    if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &program)) fail("PR_SET_SECCOMP");
}
int main(int argc, char **argv) {
    if (argc<2) { fprintf(stderr,"usage: no_network executable [args...]\n"); return 64; }
    close_inherited();
    drop_capabilities();
    install_filter();
    if (setenv("SK_REFERENCE_LAUNCHER", "linux-seccomp-v1", 1)) fail("setenv");
    execvp(argv[1], argv+1);
    fail("execvp");
    return 78;
}
