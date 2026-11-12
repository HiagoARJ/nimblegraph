#pragma once

namespace game {
class Entity {
public:
    virtual ~Entity() = default;
    virtual void update(float dt) = 0;
};
}
