#pragma once
#include <vector>
#include "entity.hpp"

namespace game {
class World {
public:
    void step(float dt);
    void add(Entity *e);
private:
    std::vector<Entity *> entities_;
};
}
