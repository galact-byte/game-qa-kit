#pragma once
#include <cstddef>
namespace gameqa {
inline bool usable_bgr(const unsigned char* pixels,unsigned width,unsigned height,std::ptrdiff_t stride) {
    if(!pixels || !width || !height) return false;
    bool black=true,white=true;
    for(unsigned y=0;y<height;++y) {
        const auto* row=pixels+static_cast<std::ptrdiff_t>(y)*stride;
        for(unsigned x=0;x<width;++x) {
            const auto* p=row+static_cast<std::size_t>(x)*3;
            black=black && p[0]==0 && p[1]==0 && p[2]==0;
            white=white && p[0]==255 && p[1]==255 && p[2]==255;
            if(!black && !white) return true;
        }
    }
    return false;
}
}
