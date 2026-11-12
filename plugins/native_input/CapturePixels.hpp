#pragma once
#include <algorithm>
#include <cstddef>
namespace native_input {
inline bool usable_pixels(const unsigned char* pixels,int width,int height) {
    if(width<=2||height<=2)return false;
    size_t black=0,white=0,sampled=0;
    const size_t inner_width=static_cast<size_t>(width-2),total=inner_width*(height-2);
    const size_t step=(std::max)(size_t(1),total/20000);
    // The compositor can add a one-pixel perimeter even with PW_CLIENTONLY.
    for(size_t i=0;i<total;i+=step){
        const auto p=pixels+((i/inner_width+1)*width+i%inner_width+1)*4;
        if(p[0]==0&&p[1]==0&&p[2]==0)++black;
        else if(p[0]==255&&p[1]==255&&p[2]==255)++white;
        ++sampled;
    }
    return sampled&&static_cast<double>((std::max)(black,white))/sampled<0.999;
}
}
