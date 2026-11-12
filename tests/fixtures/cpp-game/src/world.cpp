#include "world.hpp"

namespace game {
void World::step(float dt) {
    for (auto *e : entities_) e->update(dt);
}

void World::add(Entity *e) { entities_.push_back(e); }
}
