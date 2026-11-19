#include "bridge_state.h"
#include <iostream>
#include <stdexcept>

#define CHECK(value) do { if (!(value)) throw std::runtime_error(#value); } while (0)

int main() {
    using namespace GameQa;
    State state(71);
    Command command{71, 1, Kind::Down, 2000, 1200};
    CHECK(!state.Poll(command, 1000));
    CHECK(state.paused && !state.held && state.ack_seq == 0);
    command = {71, 2, Kind::Resume, 2000, 1200};
    CHECK(!state.Poll(command, 1000));
    CHECK(state.ack_seq == 0);
    state.Commit(1);
    CHECK(!state.paused && state.ack_seq == 2);
    command = {72, 3, Kind::Down, 2000, 1200};
    CHECK(!state.Poll(command, 1000));
    state.Commit(2);
    CHECK(state.ack_seq == 2);
    command = {71, 4, Kind::Down, 2000, 999};
    CHECK(!state.Poll(command, 1000));
    state.Commit(3);
    CHECK(state.ack_seq == 2);
    command = {71, 5, Kind::Resume, 2000, 1200};
    state.Poll(command, 1000); state.Commit(4);
    command = {71, 6, Kind::Down, 2000, 1200};
    CHECK(state.Poll(command, 1000));
    CHECK(state.ack_seq == 5);
    state.Commit(5);
    CHECK(state.ack_seq == 6 && state.ack_kind == Kind::Down && state.held);
    CHECK(!state.Poll(command, 2001));
    state.Commit(5);
    CHECK(!state.held && state.paused);
    command.lease_until = 3000;
    command.expires = 3000;
    CHECK(!state.Poll(command, 2100));
    state.Commit(6);
    CHECK(state.ack_seq == 6 && state.paused);
    command = {71, 7, Kind::Resume, 3000, 2300};
    state.Poll(command, 2100); state.Commit(7);
    command = {71, 8, Kind::Down, 3000, 2300};
    CHECK(state.Poll(command, 2100)); state.Commit(8);
    command = {71, 9, Kind::Up, 3000, 2300};
    CHECK(!state.Poll(command, 2100));
    CHECK(state.ack_seq == 8);
    state.Commit(9);
    CHECK(state.ack_seq == 9 && state.ack_kind == Kind::Up && !state.held);
    command = {71, 10, Kind::Pause, 3000, 2300};
    state.Poll(command, 2100); state.Commit(10);
    CHECK(state.paused && !state.held);
    command = {71, 11, static_cast<Kind>(999), 3000, 2300};
    CHECK(!state.Poll(command, 2100)); state.Commit(11);
    CHECK(state.ack_seq == 10);
    command = {71, 12, Kind::Resume, 10000, 2300};
    CHECK(!state.Poll(command, 2100)); state.Commit(12);
    CHECK(state.paused && state.ack_seq == 10);
    std::cout << "EasyRPG production bridge state checks passed\n";
}
