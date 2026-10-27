#include "world/motion_frame_selection.hpp"
#include <stdexcept>
#include <string>

using spatial::identity::v1::WorldSessionRequest;
using spatial::world_detail::motion_after_key;

void check(bool value, const char* message) { if(!value)throw std::runtime_error(message); }
std::string frame(const WorldSessionRequest& request) {
    const auto bytes=request.SerializeAsString(); const auto n=bytes.size();
    std::string result(4,'\0');
    result[0]=static_cast<char>(n>>24);result[1]=static_cast<char>(n>>16);
    result[2]=static_cast<char>(n>>8);result[3]=static_cast<char>(n);
    return result+bytes;
}
int main() {
    WorldSessionRequest key;key.set_protocol_version(1);key.set_request_id(1);
    key.mutable_action()->set_seq(1);key.mutable_action()->mutable_key()->set_keysym("a");
    key.mutable_action()->mutable_key()->set_pressed(true);
    WorldSessionRequest motion;motion.set_protocol_version(1);motion.set_request_id(2);
    motion.mutable_motion_refresh()->set_refresh_sequence(1);
    motion.mutable_motion_refresh()->mutable_input()->add_axes(1);
    const auto prefix=frame(key);const auto suffix=frame(motion);
    const auto chosen=motion_after_key(prefix+suffix,prefix.size()-4,key);
    check(chosen && chosen->offset==prefix.size() && chosen->length==suffix.size()-4,"valid refresh not selected");
    for(std::size_t n=0;n<suffix.size();++n)
        check(!motion_after_key(prefix+suffix.substr(0,n),prefix.size()-4,key),"partial frame selected");
    WorldSessionRequest stop=key;stop.mutable_action()->mutable_stop();
    check(!motion_after_key(frame(stop)+suffix,frame(stop).size()-4,stop),"passed a stop");
    WorldSessionRequest close;close.set_protocol_version(1);close.set_request_id(1);close.mutable_close();
    check(!motion_after_key(frame(close)+suffix,frame(close).size()-4,close),"passed close");
    WorldSessionRequest first=key;first.mutable_action()->set_token("untrusted");
    check(!motion_after_key(frame(first)+suffix,frame(first).size()-4,first),"passed invalid token");
    first=key;first.mutable_action()->clear_seq();
    check(!motion_after_key(frame(first)+suffix,frame(first).size()-4,first),"passed unsequenced key");
    WorldSessionRequest stale=motion;stale.set_request_id(1);
    check(!motion_after_key(prefix+frame(stale),prefix.size()-4,key),"selected stale request id");
    WorldSessionRequest barrier;barrier.set_protocol_version(1);barrier.set_request_id(2);
    barrier.mutable_action()->set_seq(2);barrier.mutable_action()->mutable_stop();
    check(!motion_after_key(prefix+frame(barrier)+suffix,prefix.size()-4,key),"passed intervening stop");
    std::string oversize=prefix+std::string(4,'\xff');
    check(!motion_after_key(oversize,prefix.size()-4,key),"selected oversized frame");
    return 0;
}
