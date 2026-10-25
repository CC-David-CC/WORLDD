#include "world/actors.hpp"
#include "world/surfaces.hpp"
#include "world/store.hpp"
#include "platform/render_worker.hpp"
#include <chrono>
#include <fstream>
#include <iostream>
#include <poll.h>
#include <stdexcept>

namespace {
using Clock=std::chrono::steady_clock;
void check(bool value,const char* message){if(!value)throw std::runtime_error(message);}
template<class Function> void denied(Function function,spatial::wire::ErrorCode code) {
    try{function();}catch(const spatial::WorldError& error){check(error.code==code,"Unexpected actor denial code");return;}
    throw std::runtime_error("Actor operation unexpectedly succeeded");
}
template<class Function> void until(Function function) {
    const auto deadline=Clock::now()+std::chrono::seconds(3);
    while(!function()) {check(Clock::now()<deadline,"Fixture progress deadline exceeded");::poll(nullptr,0,1);}
}
}

int main(int argc,char** argv) {
    try {
        check(argc==4,"Expected peer, state directory and scenario");
        const std::filesystem::path root=argv[2];const std::string scenario=argv[3];
        const bool abandoned=scenario=="abandoned",close_denied=scenario=="close-denied";
        const bool close_lost=scenario=="close-lost",cancel_denied=scenario=="cancel-denied";
        check(scenario=="checked-close" || abandoned || close_denied || close_lost || cancel_denied,"Unknown scenario");
        std::filesystem::create_directories(root/"renderer");
        spatial::WorldStore store(root/"world");spatial::Surfaces surfaces(store);
        spatial::RenderWorker renderer(argv[1],root/"renderer",":protocol-fixture");
        spatial::wire::RuntimeConfig config;config.set_linear_gain(1);config.set_angular_gain(1);
        config.set_deadzone(.05);config.set_input_timeout(.25);config.set_actor_radius(.2);config.set_interaction_reach(3);
        spatial::Actors actors(store,config,true,&surfaces,&renderer);
        spatial::wire::ActorOpenRequest open;open.set_id("actor-one");open.add_grants("navigate");
        const auto first=actors.open(open);
        spatial::wire::ActorOpenRequest second_open=open;second_open.set_id("actor-two");
        second_open.mutable_pose()->mutable_position()->set_x(1);second_open.mutable_pose()->mutable_orientation()->set_w(1);
        const auto second=actors.open(second_open);
        // The peer supplies only a native identity, through the ordinary snapshot parser.
        (void)actors.inspect(first.token());
        spatial::wire::ActorActionRequest motion;motion.set_token(first.token());motion.set_seq(1);
        for(unsigned n=0;n<6;++n)motion.mutable_input()->add_axes(n==0?1:0);
        check(actors.act(motion).accepted(),"Initial movement was refused");
        spatial::wire::ActorActionRequest stop;stop.set_token(first.token());stop.set_seq(2);stop.mutable_stop();
        const auto ticket=actors.begin_stop(stop);
        denied([&]{(void)actors.begin_stop(stop);},spatial::wire::IN_USE);
        const auto progress=[&] {
            const auto started=Clock::now();const bool pending=actors.progress_stops();
            check(Clock::now()-started<std::chrono::milliseconds(250),"Actor cancellation progress blocked");
            return pending;
        };
        until([&]{progress();return std::filesystem::exists(root/"renderer/pending-cancel");});
        check(!actors.poll_stop(ticket),"Unconfirmed Stop was accepted");
        motion.set_token(second.token());check(actors.act(motion).accepted(),"Other actor movement was refused");
        spatial::wire::ClockAdvanceRequest clock;clock.set_seconds(.05);actors.advance(clock);
        check(actors.find(first.id())->pose().position().x()==0,"Stopped actor retained movement");
        check(actors.find(second.id())->pose().position().x()>1,"Other actor movement did not progress");
        if(cancel_denied) {
            std::ofstream(root/"renderer/release-cancel").put('1');
            until([&]{progress();try{(void)actors.poll_stop(ticket);return false;}
                catch(const spatial::WorldError& error){check(error.code==spatial::wire::FORBIDDEN,"Wrong Cancel denial");return true;}});
            check(!renderer.busy() && !renderer.cancellation_pending(),"Typed denial retained native stream");
        }
        if(abandoned)actors.abandon_stop(ticket);
        check(actors.close(first.token()).closed(),"Logical actor close was refused");
        denied([&]{(void)actors.open(open);},spatial::wire::IN_USE);
        std::ofstream(root/"renderer/release-cancel").put('1');
        until([&]{progress();return std::filesystem::exists(root/"renderer/pending-close");});
        check(!actors.cancellation_settled(),"Pending Close was treated as settled");
        denied([&]{(void)actors.open(open);},spatial::wire::IN_USE);
        std::ofstream(root/"renderer/release-close").put('1');
        if(close_denied || close_lost) {
            until([&]{progress();try{(void)actors.poll_stop(ticket);return false;}
                catch(const spatial::WorldError& error){check(close_denied && error.code==spatial::wire::FORBIDDEN,"Wrong Close denial");return true;}
                catch(const std::runtime_error&){check(close_lost,"Unexpected Close transport failure");return true;}});
            for(unsigned n=0;n<32;++n)progress();
            check(!actors.cancellation_settled(),"Failed Close lost its cleanup barrier");
            check(renderer.cancellation_pending()==close_lost,"Uncertain Close lost its native owner");
            denied([&]{(void)actors.open(open);},spatial::wire::IN_USE);
        } else {
            until([&]{progress();return actors.cancellation_settled();});
            denied([&]{(void)actors.poll_stop(ticket);},abandoned?spatial::wire::STALE_TARGET:spatial::wire::UNAUTHORIZED);
            const auto next=actors.open(open);check(next.token()!=first.token(),"Actor session token was reused");
            denied([&]{(void)actors.poll_stop(ticket);},spatial::wire::STALE_TARGET);
            motion.set_token(next.token());motion.set_seq(1);check(actors.act(motion).accepted(),"New actor incarnation was unusable");
        }
        std::cout<<"PASS "<<scenario<<'\n';
    }catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 2;}
}
