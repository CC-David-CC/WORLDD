#include "world/storage_vfs.hpp"
#include <sqlite3.h>
#include <filesystem>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>

namespace {
void require(bool valid) { if (!valid) throw std::runtime_error("Storage VFS witness failed"); }
using Database = std::unique_ptr<sqlite3, decltype(&sqlite3_close)>;
Database open(const std::filesystem::path& path, const char* vfs) {
    sqlite3* raw = nullptr;
    const int rc = sqlite3_open_v2(path.c_str(), &raw, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, vfs);
    Database result(raw, sqlite3_close); require(rc == SQLITE_OK); return result;
}
int sql(sqlite3* db, const char* statement) { return sqlite3_exec(db, statement, nullptr, nullptr, nullptr); }
int value(sqlite3* db) {
    sqlite3_stmt* raw = nullptr;
    require(sqlite3_prepare_v2(db, "SELECT v FROM data", -1, &raw, nullptr) == SQLITE_OK);
    std::unique_ptr<sqlite3_stmt, decltype(&sqlite3_finalize)> statement(raw, sqlite3_finalize);
    require(sqlite3_step(raw) == SQLITE_ROW); return sqlite3_column_int(raw, 0);
}
}
int main(int argc, char** argv) {
    try {
        require(argc == 2);
        const std::filesystem::path root(argv[1]); require(root.is_absolute());
        std::filesystem::create_directories(root);
        auto* original = sqlite3_vfs_find(nullptr);
        for (int cycle = 0; cycle < 16; ++cycle) {
            std::string first_name, second_name;
            {
                spatial::StorageVfs first, second;
                first_name = first.name(); second_name = second.name();
                require(first_name != second_name && sqlite3_vfs_find(nullptr) == original);
                auto a = open(root / ("first-" + std::to_string(cycle)), first.name());
                auto b = open(root / ("second-" + std::to_string(cycle)), second.name());
                auto normal = open(root / ("normal-" + std::to_string(cycle)), nullptr);
                for (auto* db : {a.get(), b.get(), normal.get()})
                    require(sql(db, "PRAGMA synchronous=EXTRA; CREATE TABLE data(v INTEGER); INSERT INTO data VALUES(1)") == SQLITE_OK);
                first.arm(); require(value(a.get()) == 1 && first.armed());
                require(sql(b.get(), "UPDATE data SET v=2") == SQLITE_OK && first.armed());
                require(sql(normal.get(), "UPDATE data SET v=3") == SQLITE_OK && first.armed());
                require(sql(a.get(), "UPDATE data SET v=4") == SQLITE_FULL && !first.armed());
                require(value(a.get()) == 1 && value(b.get()) == 2 && value(normal.get()) == 3);
                require(sql(a.get(), "UPDATE data SET v=5") == SQLITE_OK && value(a.get()) == 5);
                // Exercise forwarded shared-memory methods with real WAL I/O too.
                require(sql(a.get(), "PRAGMA journal_mode=WAL; UPDATE data SET v=6; PRAGMA wal_checkpoint(TRUNCATE)") == SQLITE_OK);
                require(value(a.get()) == 6);
            }
            require(sqlite3_vfs_find(first_name.c_str()) == nullptr);
            require(sqlite3_vfs_find(second_name.c_str()) == nullptr);
            require(sqlite3_vfs_find(nullptr) == original);
            auto reopened = open(root / ("first-" + std::to_string(cycle)), nullptr);
            require(value(reopened.get()) == 6);
        }
        std::cout << "16 VFS lifecycle, isolated-write, retry, WAL, and reopen cycles passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
