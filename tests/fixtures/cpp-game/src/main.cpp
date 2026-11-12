#include <iostream>
#include "player.hpp"
#include "world.hpp"

int main() {
    game::World world;
    game::Player player(&world);
    world.add(&player);
    world.step(0.016f);
    std::cout << "ok" << std::endl;
    return 0;
}
