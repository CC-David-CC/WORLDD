#pragma once
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <unistd.h>

namespace spatial::stop_trace {
inline bool enabled() noexcept {
    static const bool value=[] {const auto* flag=std::getenv("SPATIAL_TRACE_STOP");
        return flag && std::strcmp(flag,"1")==0;}();
    return value;
}
inline std::uint64_t now() noexcept {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}
class Scope;
inline thread_local Scope* active=nullptr;
// Diagnostic timestamps never change deadlines, authority, or operation outcomes.
// Each scope buffers fixed-size records and writes once after leaving its operation.
class Scope final {
    struct Event {const char* phase;std::uint64_t time,value;};
    Event events_[32]{};
    unsigned count_=0,dropped_=0;
    const char* role_;
    std::uint64_t id_,parent_;
    Scope* previous_;
public:
    Scope(const char* role,std::uint64_t id) noexcept
        :role_(role && enabled()?role:nullptr),id_(id),parent_(active?active->id_:0),previous_(active) {
        if(role_){active=this;mark("begin");}
    }
    Scope(const Scope&)=delete;
    Scope& operator=(const Scope&)=delete;
    void mark(const char* phase,std::uint64_t value=0) noexcept {
        if(!role_)return;
        if(count_<32)events_[count_++]={phase,now(),value};else ++dropped_;
    }
    ~Scope() noexcept {
        if(!role_)return;
        mark("end");active=previous_;
        char output[8192];std::size_t used=0;
        for(unsigned n=0;n<count_;++n) {
            const auto& e=events_[n];const int size=std::snprintf(output+used,sizeof(output)-used,
                "stop_trace pid=%d role=%s id=%llu parent=%llu phase=%s mono_ns=%llu value=%llu dropped=%u\n",
                int(getpid()),role_,static_cast<unsigned long long>(id_),static_cast<unsigned long long>(parent_),
                e.phase,static_cast<unsigned long long>(e.time),static_cast<unsigned long long>(e.value),dropped_);
            if(size<0 || std::size_t(size)>=sizeof(output)-used)break;
            used+=std::size_t(size);
        }
        if(used)(void)::write(STDERR_FILENO,output,used);
    }
};
inline void mark(const char* phase,std::uint64_t value=0) noexcept {
    if(active)active->mark(phase,value);
}
}
