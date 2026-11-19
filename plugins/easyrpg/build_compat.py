"""Compatibility edits for the approved 2026 official static dependency export."""
from pathlib import Path


FMT_COMPAT = '''
#include <type_traits>

namespace fmt {
template <typename T>
struct formatter<T, char, std::enable_if_t<std::is_enum_v<T>>>
    : formatter<std::underlying_type_t<T>> {
    template <typename Context>
    auto format(T value, Context& ctx) const {
        return formatter<std::underlying_type_t<T>>::format(
            static_cast<std::underlying_type_t<T>>(value), ctx);
    }
};
template <>
struct formatter<lcf::DBString> : formatter<string_view> {
    template <typename Context>
    auto format(const lcf::DBString& value, Context& ctx) const {
        return formatter<string_view>::format(string_view(value.data(), value.size()), ctx);
    }
};
}
'''


def apply(player):
    player = Path(player)
    for path in (player/'CMakeLists.txt',player/'lib/liblcf/CMakeLists.txt'):
        text = path.read_text(encoding='utf-8')
        if text.count('set(CMAKE_CXX_STANDARD 14)') != 1:
            raise ValueError('Unexpected upstream C++ standard: '+str(path))
        path.write_text(text.replace('set(CMAKE_CXX_STANDARD 14)','set(CMAKE_CXX_STANDARD 17)'),
                        encoding='utf-8',newline='\n')
    path = player/'src/output.h'
    text = path.read_text(encoding='utf-8')
    marker = '#include <lcf/dbstring.h>'
    if text.count(marker) != 1:
        raise ValueError('Unexpected upstream output header')
    path.write_text(text.replace(marker,marker+'\n'+FMT_COMPAT),encoding='utf-8',newline='\n')
