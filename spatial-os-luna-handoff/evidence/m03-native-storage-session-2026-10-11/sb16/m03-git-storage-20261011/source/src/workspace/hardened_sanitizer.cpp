// Only the namespace initializer links this hook. Its inherited policy denies
// ptrace, which GCC LeakSanitizer requires and cannot override safely.
// AddressSanitizer and UBSan remain active here. Test the same initializer body
// with full LeakSanitizer through spatial-app-initializer-probe outside that policy.
#if defined(__SANITIZE_ADDRESS__)
extern "C" int __lsan_is_turned_off() { return 1; }
#endif
