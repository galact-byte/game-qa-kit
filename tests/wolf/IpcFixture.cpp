#include "Realtime.hpp"
#include "Wire.hpp"
#include <chrono>
#include <atomic>
#include <iostream>
#include <thread>
int main(int argc,char** argv) {
    if(argc!=2 && argc!=3) return 2;
    wolf::Realtime mailbox(GetCurrentProcessId(),argv[1]);
    mailbox.write(wolf::Realtime::Status,"native-ready");
    std::atomic<bool> done{false};
    std::thread worker([&] {
        std::string last;long long frame=10;double expiry=0;bool expired=false;
        while(!done) {
            const auto value=mailbox.read(wolf::Realtime::Request);
            if(argc==2) {
                if(!value.empty()) mailbox.write(wolf::Realtime::Result,value);
            } else {
                using gameqa::quote;
                const double now=std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
                expiry=gameqa::lease_expiry(expiry,argv[1],gameqa::ini(mailbox.read(wolf::Realtime::Lease)));
                if(expiry>0 && expiry<now) expired=true;
                const auto request=gameqa::ini(value);const auto id=gameqa::get(request,"id");
                if(!id.empty() && id!=last) {
                    last=id;++frame;
                    mailbox.write(wolf::Realtime::Result,"{\"protocol\":1,\"session_id\":"+quote(argv[1])+
                        ",\"id\":"+quote(id)+",\"ok\":true,\"consumed\":true,\"pause_id\":\"\",\"frame\":"+std::to_string(frame)+
                        ",\"capture_frame\":"+std::to_string(frame)+",\"screenshot\":"+quote("screenshots/"+id+".png")+"}");
                }
                std::ostringstream state;
                state<<"{\"protocol\":1,\"session_id\":"<<quote(argv[1])<<",\"pid\":"<<GetCurrentProcessId()
                    <<",\"exe_path\":"<<quote(argv[2])<<",\"frame\":"<<frame<<",\"updated_at\":"<<std::setprecision(16)<<now
                    <<",\"paused\":"<<(expired?"true":"false")<<",\"lease_expired\":"<<(expired?"true":"false")
                    <<",\"input_ready\":true,\"capabilities\":[\"wait\"],\"held\":[]}";
                mailbox.write(wolf::Realtime::Status,state.str());
            }
            Sleep(2);
        }
    });
    std::cout<<"ready"<<std::endl;
    std::string line;std::getline(std::cin,line);done=true;worker.join();return 0;
}
