#pragma once
#include "ControlCore.hpp"
#include <cerrno>
#include <cstdlib>
#include <iomanip>
#include <map>
#include <sstream>
#include <stdexcept>
namespace gameqa {
using Fields=std::map<std::string,std::string>;
inline std::string trim(const std::string& s) {
    const auto a=s.find_first_not_of(" \r\t");
    return a==std::string::npos?"":s.substr(a,s.find_last_not_of(" \r\t")-a+1);
}
inline Fields ini(const std::string& text) {
    if(text.size()>65536) throw std::runtime_error("control_file_too_large");
    Fields fields;std::istringstream input(text);std::string line;bool section=false;
    while(std::getline(input,line)) {
        line=trim(line);
        if(line.empty() || line[0]=='#' || line[0]==';') continue;
        if(line=="[bridge]") { if(section) throw std::runtime_error("duplicate_section");section=true;continue; }
        const auto split=line.find('=');
        if(!section || split==std::string::npos || !split) throw std::runtime_error("invalid_ini");
        if(!fields.emplace(trim(line.substr(0,split)),trim(line.substr(split+1))).second)
            throw std::runtime_error("duplicate_field");
    }
    return fields;
}
inline std::string get(const Fields& fields,const std::string& key) {
    const auto i=fields.find(key);return i==fields.end()?"":i->second;
}
inline long long integer(const Fields& fields,const std::string& key) {
    auto s=get(fields,key);char* end=nullptr;errno=0;auto value=std::strtoll(s.c_str(),&end,10);
    if(s.empty() || errno || *end) throw std::runtime_error("invalid_integer");return value;
}
inline int small_integer(const Fields& fields,const std::string& key) {
    const auto value=integer(fields,key);
    if(value<(-2147483647LL-1) || value>2147483647) throw std::runtime_error("integer_overflow");
    return static_cast<int>(value);
}
inline double number(const Fields& fields,const std::string& key) {
    auto s=get(fields,key);char* end=nullptr;errno=0;auto value=std::strtod(s.c_str(),&end);
    if(s.empty() || errno || *end || !std::isfinite(value)) throw std::runtime_error("invalid_number");return value;
}
inline double lease_expiry(double previous,const std::string& session,const Fields& fields) {
    // A failed read cannot renew a lease or erase its still-valid absolute deadline.
    return get(fields,"session_id")==session?number(fields,"expires_at"):previous;
}
inline Command parse_command(const Fields& f) {
    Command c;c.action=get(f,"payload.action");
    std::set<std::string> allowed={"protocol","session_id","id","pause_id","after_frame","created_at","payload.action","payload.frames"};
    if(c.action=="move") allowed.insert("payload.direction");
    if(c.action=="press" || c.action=="release") allowed.insert("payload.button");
    for(const auto& field:f) if(!allowed.count(field.first)) throw std::runtime_error("unknown_command_field");
    c.protocol=small_integer(f,"protocol");c.frames=small_integer(f,"payload.frames");c.after=integer(f,"after_frame");
    c.session=get(f,"session_id");c.id=get(f,"id");c.pause=get(f,"pause_id");
    c.button=get(f,"payload.button");c.direction=get(f,"payload.direction");return c;
}
inline std::string quote(const std::string& s) {
    std::ostringstream o;o<<'"';
    for(unsigned char c:s) {
        if(c=='"' || c=='\\') o<<'\\'<<c;
        else if(c<32) o<<"\\u"<<std::hex<<std::setw(4)<<std::setfill('0')<<static_cast<int>(c)<<std::dec;
        else o<<c;
    }
    o<<'"';return o.str();
}
}
