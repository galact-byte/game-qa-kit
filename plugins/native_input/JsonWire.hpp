#pragma once
#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <iomanip>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
namespace native_input {
struct Value { char type;std::string text; };
using Fields=std::map<std::string,Value>;
// Only the control envelope and its scalar payload are accepted; arrays are not part of this wire format.
class JsonReader {
    const std::string& text_;size_t pos_=0;Fields fields_;
    [[noreturn]] static void invalid(){throw std::runtime_error("invalid_control_json");}
    void space(){while(pos_<text_.size()&&(text_[pos_]==' '||text_[pos_]=='\n'||text_[pos_]=='\r'||text_[pos_]=='\t'))++pos_;}
    char take(){if(pos_==text_.size())invalid();return text_[pos_++];}
    unsigned hex(){unsigned value=0;for(int i=0;i<4;++i){const auto c=take();value*=16;
        if(c>='0'&&c<='9')value+=c-'0';else if(c>='a'&&c<='f')value+=c-'a'+10;else if(c>='A'&&c<='F')value+=c-'A'+10;else invalid();}return value;}
    static void utf8(std::string& out,unsigned value){
        if(!value||value>0x10ffff||(value>=0xd800&&value<=0xdfff))invalid();
        if(value<128)out+=static_cast<char>(value);
        else if(value<2048){out+=static_cast<char>(0xc0|(value>>6));out+=static_cast<char>(0x80|(value&63));}
        else if(value<65536){out+=static_cast<char>(0xe0|(value>>12));out+=static_cast<char>(0x80|((value>>6)&63));out+=static_cast<char>(0x80|(value&63));}
        else{out+=static_cast<char>(0xf0|(value>>18));out+=static_cast<char>(0x80|((value>>12)&63));out+=static_cast<char>(0x80|((value>>6)&63));out+=static_cast<char>(0x80|(value&63));}
    }
    std::string string(){
        if(take()!='"')invalid();std::string out;
        for(;;){const auto c=take();if(c=='"')return out;if(static_cast<unsigned char>(c)<32)invalid();
            if(c!='\\'){out+=c;continue;}
            switch(take()){
                case '"':out+='"';break;case '\\':out+='\\';break;case '/':out+='/';break;
                case 'b':out+='\b';break;case 'f':out+='\f';break;case 'n':out+='\n';break;case 'r':out+='\r';break;case 't':out+='\t';break;
                case 'u':{auto value=hex();if(value>=0xd800&&value<=0xdbff){if(take()!='\\'||take()!='u')invalid();const auto low=hex();if(low<0xdc00||low>0xdfff)invalid();value=0x10000+((value-0xd800)<<10)+(low-0xdc00);}utf8(out,value);break;}
                default:invalid();
            }
        }
    }
    Value scalar(){
        if(pos_<text_.size()&&text_[pos_]=='"')return {'s',string()};
        const auto start=pos_;while(pos_<text_.size()&&text_[pos_]!=','&&text_[pos_]!='}'&&text_[pos_]!=' '&&text_[pos_]!='\r'&&text_[pos_]!='\n'&&text_[pos_]!='\t')++pos_;
        const auto value=text_.substr(start,pos_-start);
        if(value=="true"||value=="false")return {'b',value};if(value=="null")return {'z',value};
        size_t i=0;if(i<value.size()&&value[i]=='-')++i;if(i==value.size())invalid();
        if(value[i]=='0')++i;else{if(value[i]<'1'||value[i]>'9')invalid();while(i<value.size()&&value[i]>='0'&&value[i]<='9')++i;}
        bool integer=true;
        if(i<value.size()&&value[i]=='.'){integer=false;const auto digits=++i;while(i<value.size()&&value[i]>='0'&&value[i]<='9')++i;if(i==digits)invalid();}
        if(i<value.size()&&(value[i]=='e'||value[i]=='E')){integer=false;++i;if(i<value.size()&&(value[i]=='+'||value[i]=='-'))++i;const auto digits=i;while(i<value.size()&&value[i]>='0'&&value[i]<='9')++i;if(i==digits)invalid();}
        if(i!=value.size())invalid();return {integer?'i':'n',value};
    }
    void object(const std::string& prefix){
        if(take()!='{')invalid();space();if(pos_<text_.size()&&text_[pos_]=='}'){++pos_;return;}
        std::map<std::string,bool> names;
        for(;;){space();const auto key=string();if(key.empty()||key.find('.')!=std::string::npos||!names.emplace(key,true).second)invalid();
            space();if(take()!=':')invalid();space();
            if(pos_<text_.size()&&text_[pos_]=='{'){if(!prefix.empty()||key!="payload")invalid();object(key+".");}
            else if(!fields_.emplace(prefix+key,scalar()).second)invalid();
            if(fields_.size()>32)invalid();space();const auto next=take();if(next=='}')return;if(next!=',')invalid();
        }
    }
public:
    explicit JsonReader(const std::string& text):text_(text){}
    Fields read(){if(text_.empty()||text_.size()>65536)invalid();space();object("");space();if(pos_!=text_.size())invalid();return fields_;}
};
inline Fields json(const std::string& text){return JsonReader(text).read();}
inline std::string get(const Fields& f,const std::string& key){const auto it=f.find(key);if(it==f.end())return "";if(it->second.type!='s')throw std::runtime_error("invalid_string");return it->second.text;}
inline long long integer(const Fields& f,const std::string& key){
    const auto it=f.find(key);if(it==f.end()||it->second.type!='i')throw std::runtime_error("invalid_integer");
    errno=0;char* end=nullptr;const auto value=std::strtoll(it->second.text.c_str(),&end,10);if(errno||*end)throw std::runtime_error("invalid_integer");return value;
}
inline double number(const Fields& f,const std::string& key){
    const auto it=f.find(key);if(it==f.end()||(it->second.type!='i'&&it->second.type!='n'))throw std::runtime_error("invalid_number");
    errno=0;char* end=nullptr;const auto value=std::strtod(it->second.text.c_str(),&end);if(errno||*end||!std::isfinite(value))throw std::runtime_error("invalid_number");return value;
}
inline std::string quote(const std::string& text){std::ostringstream out;out<<'"';for(unsigned char c:text){if(c=='"'||c=='\\')out<<'\\'<<c;else if(c<32)out<<"\\u"<<std::hex<<std::setw(4)<<std::setfill('0')<<static_cast<unsigned>(c)<<std::dec;else out<<c;}out<<'"';return out.str();}
}
