#pragma once
#include "entity.hpp"
#include "world.hpp"

namespace game {
class Player : public Entity {
public:
    explicit Player(World *w) : world_(w) {}
    void update(float dt) override;
private:
    World *world_;
};
}
