#include "world/storage_vfs.hpp"
#include <sqlite3.h>
#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cstddef>
#include <cstring>
#include <limits>
#include <new>
#include <stdexcept>
#include <string>
#include <type_traits>

namespace spatial {
struct StorageVfs::Impl {
    struct File {
        sqlite3_file base{};
        sqlite3_file* real = nullptr;
        Impl* owner = nullptr;
        sqlite3_io_methods methods{};
    };
    static_assert(std::is_standard_layout_v<File>);
    static constexpr std::size_t offset = (sizeof(File) + alignof(std::max_align_t) - 1) /
                                         alignof(std::max_align_t) * alignof(std::max_align_t);
    sqlite3_vfs* parent = nullptr;
    sqlite3_vfs vfs{};
    std::string label;
    bool pending = false;

    static Impl& owner(sqlite3_vfs* v) { return *static_cast<Impl*>(v->pAppData); }
    static File& file(sqlite3_file* f) { return *reinterpret_cast<File*>(f); }
    static int close(sqlite3_file* f) {
        auto& wrapped = file(f);
        const int rc = wrapped.real->pMethods->xClose(wrapped.real);
        wrapped.base.pMethods = nullptr;
        return rc;
    }
    static int read(sqlite3_file* f, void* data, int size, sqlite3_int64 at) {
        auto* real = file(f).real; return real->pMethods->xRead(real, data, size, at);
    }
    static int write(sqlite3_file* f, const void* data, int size, sqlite3_int64 at) {
        auto& wrapped = file(f);
        if (size > 0 && wrapped.owner->pending) {
            wrapped.owner->pending = false;
            errno = ENOSPC;
            return SQLITE_FULL;
        }
        return wrapped.real->pMethods->xWrite(wrapped.real, data, size, at);
    }
    static int truncate(sqlite3_file* f, sqlite3_int64 size) {
        auto* real = file(f).real; return real->pMethods->xTruncate(real, size);
    }
    static int sync(sqlite3_file* f, int flags) {
        auto* real = file(f).real; return real->pMethods->xSync(real, flags);
    }
    static int size(sqlite3_file* f, sqlite3_int64* out) {
        auto* real = file(f).real; return real->pMethods->xFileSize(real, out);
    }
    static int lock(sqlite3_file* f, int kind) {
        auto* real = file(f).real; return real->pMethods->xLock(real, kind);
    }
    static int unlock(sqlite3_file* f, int kind) {
        auto* real = file(f).real; return real->pMethods->xUnlock(real, kind);
    }
    static int reserved(sqlite3_file* f, int* out) {
        auto* real = file(f).real; return real->pMethods->xCheckReservedLock(real, out);
    }
    static int control(sqlite3_file* f, int op, void* arg) {
        auto* real = file(f).real; return real->pMethods->xFileControl(real, op, arg);
    }
    static int sector(sqlite3_file* f) {
        auto* real = file(f).real; return real->pMethods->xSectorSize(real);
    }
    static int characteristics(sqlite3_file* f) {
        auto* real = file(f).real; return real->pMethods->xDeviceCharacteristics(real);
    }
    static int map(sqlite3_file* f, int page, int size, int extend, void volatile** out) {
        auto* real = file(f).real; return real->pMethods->xShmMap(real, page, size, extend, out);
    }
    static int shm_lock(sqlite3_file* f, int at, int count, int flags) {
        auto* real = file(f).real; return real->pMethods->xShmLock(real, at, count, flags);
    }
    static void barrier(sqlite3_file* f) {
        auto* real = file(f).real; real->pMethods->xShmBarrier(real);
    }
    static int unmap(sqlite3_file* f, int remove) {
        auto* real = file(f).real; return real->pMethods->xShmUnmap(real, remove);
    }
    static int fetch(sqlite3_file* f, sqlite3_int64 at, int size, void** out) {
        auto* real = file(f).real; return real->pMethods->xFetch(real, at, size, out);
    }
    static int unfetch(sqlite3_file* f, sqlite3_int64 at, void* data) {
        auto* real = file(f).real; return real->pMethods->xUnfetch(real, at, data);
    }
    static int open(sqlite3_vfs* v, sqlite3_filename name, sqlite3_file* f, int flags, int* out) {
        auto& self = owner(v);
        auto* wrapped = new (f) File{};
        wrapped->owner = &self;
        wrapped->real = reinterpret_cast<sqlite3_file*>(reinterpret_cast<unsigned char*>(f) + offset);
        std::memset(wrapped->real, 0, static_cast<std::size_t>(self.parent->szOsFile));
        const int rc = self.parent->xOpen(self.parent, name, wrapped->real, flags, out);
        const auto* original = wrapped->real->pMethods;
        if (!original) return rc;
        // Preserve the underlying method version and optional capabilities.
        auto& m = wrapped->methods;
        m.iVersion = std::min(original->iVersion, 3);
        m.xClose = close; m.xRead = read; m.xWrite = write; m.xTruncate = truncate;
        m.xSync = sync; m.xFileSize = size; m.xLock = lock; m.xUnlock = unlock;
        m.xCheckReservedLock = reserved; m.xFileControl = control;
        m.xSectorSize = sector; m.xDeviceCharacteristics = characteristics;
        if (m.iVersion >= 2) {
            m.xShmMap = original->xShmMap ? map : nullptr;
            m.xShmLock = original->xShmLock ? shm_lock : nullptr;
            m.xShmBarrier = original->xShmBarrier ? barrier : nullptr;
            m.xShmUnmap = original->xShmUnmap ? unmap : nullptr;
        }
        if (m.iVersion >= 3) {
            m.xFetch = original->xFetch ? fetch : nullptr;
            m.xUnfetch = original->xUnfetch ? unfetch : nullptr;
        }
        wrapped->base.pMethods = &m;
        return rc;
    }
    static int remove(sqlite3_vfs* v, const char* name, int sync) {
        auto* p = owner(v).parent; return p->xDelete(p, name, sync);
    }
    static int access(sqlite3_vfs* v, const char* name, int flags, int* out) {
        auto* p = owner(v).parent; return p->xAccess(p, name, flags, out);
    }
    static int full_path(sqlite3_vfs* v, const char* name, int size, char* out) {
        auto* p = owner(v).parent; return p->xFullPathname(p, name, size, out);
    }
    static void* dl_open(sqlite3_vfs* v, const char* name) {
        auto* p = owner(v).parent; return p->xDlOpen(p, name);
    }
    static void dl_error(sqlite3_vfs* v, int size, char* out) {
        auto* p = owner(v).parent; p->xDlError(p, size, out);
    }
    static auto dl_sym(sqlite3_vfs* v, void* handle, const char* name) -> void (*)(void) {
        auto* p = owner(v).parent; return p->xDlSym(p, handle, name);
    }
    static void dl_close(sqlite3_vfs* v, void* handle) {
        auto* p = owner(v).parent; p->xDlClose(p, handle);
    }
    static int randomness(sqlite3_vfs* v, int size, char* out) {
        auto* p = owner(v).parent; return p->xRandomness(p, size, out);
    }
    static int sleep(sqlite3_vfs* v, int usec) {
        auto* p = owner(v).parent; return p->xSleep(p, usec);
    }
    static int time(sqlite3_vfs* v, double* out) {
        auto* p = owner(v).parent; return p->xCurrentTime(p, out);
    }
    static int last_error(sqlite3_vfs* v, int size, char* out) {
        auto* p = owner(v).parent; return p->xGetLastError(p, size, out);
    }
    static int time64(sqlite3_vfs* v, sqlite3_int64* out) {
        auto* p = owner(v).parent; return p->xCurrentTimeInt64(p, out);
    }
    static int set_call(sqlite3_vfs* v, const char* name, sqlite3_syscall_ptr call) {
        auto* p = owner(v).parent; return p->xSetSystemCall(p, name, call);
    }
    static sqlite3_syscall_ptr get_call(sqlite3_vfs* v, const char* name) {
        auto* p = owner(v).parent; return p->xGetSystemCall(p, name);
    }
    static const char* next_call(sqlite3_vfs* v, const char* name) {
        auto* p = owner(v).parent; return p->xNextSystemCall(p, name);
    }
    Impl() {
        parent = sqlite3_vfs_find(nullptr);
        if (!parent || parent->iVersion < 1 || parent->szOsFile <= 0 ||
            static_cast<std::size_t>(parent->szOsFile) > std::numeric_limits<int>::max() - offset)
            throw std::runtime_error("No compatible SQLite VFS");
        static std::atomic<unsigned long long> sequence{0};
        do { label = "spatial-write-fault-" + std::to_string(++sequence); }
        while (sqlite3_vfs_find(label.c_str()));
        vfs.iVersion = std::min(parent->iVersion, 3);
        vfs.szOsFile = static_cast<int>(offset + static_cast<std::size_t>(parent->szOsFile));
        vfs.mxPathname = parent->mxPathname; vfs.zName = label.c_str(); vfs.pAppData = this;
        vfs.xOpen = open; vfs.xDelete = remove; vfs.xAccess = access; vfs.xFullPathname = full_path;
        vfs.xDlOpen = parent->xDlOpen ? dl_open : nullptr;
        vfs.xDlError = parent->xDlError ? dl_error : nullptr;
        vfs.xDlSym = parent->xDlSym ? dl_sym : nullptr;
        vfs.xDlClose = parent->xDlClose ? dl_close : nullptr;
        vfs.xRandomness = randomness; vfs.xSleep = sleep; vfs.xCurrentTime = time;
        vfs.xGetLastError = parent->xGetLastError ? last_error : nullptr;
        if (vfs.iVersion >= 2) vfs.xCurrentTimeInt64 = parent->xCurrentTimeInt64 ? time64 : nullptr;
        if (vfs.iVersion >= 3) {
            vfs.xSetSystemCall = parent->xSetSystemCall ? set_call : nullptr;
            vfs.xGetSystemCall = parent->xGetSystemCall ? get_call : nullptr;
            vfs.xNextSystemCall = parent->xNextSystemCall ? next_call : nullptr;
        }
        if (sqlite3_vfs_register(&vfs, 0) != SQLITE_OK) throw std::runtime_error("Cannot register SQLite VFS");
    }
    ~Impl() { sqlite3_vfs_unregister(&vfs); }
};
StorageVfs::StorageVfs() : impl_(std::make_unique<Impl>()) {}
StorageVfs::~StorageVfs() = default;
const char* StorageVfs::name() const { return impl_->label.c_str(); }
bool StorageVfs::armed() const { return impl_->pending; }
void StorageVfs::arm() {
    if (impl_->pending) throw std::logic_error("A storage fault is already armed");
    impl_->pending = true;
}
} // namespace spatial
