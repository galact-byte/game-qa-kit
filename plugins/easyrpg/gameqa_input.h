// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef GAMEQA_EASYRPG_INPUT_H
#define GAMEQA_EASYRPG_INPUT_H
#include "input_source.h"
namespace GameQa {
std::unique_ptr<Input::Source> CreateManaged(const Game_ConfigInput&, Input::DirectionMappingArray);
void AfterScene();
}
#endif
