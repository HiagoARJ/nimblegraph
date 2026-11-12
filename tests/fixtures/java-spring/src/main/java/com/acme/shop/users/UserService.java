package com.acme.shop.users;

import com.acme.shop.users.dto.CreateUserRequest;
import java.util.List;
import org.springframework.stereotype.Service;

@Service
public class UserService {
    private final UserRepository repository;

    public UserService(UserRepository repository) {
        this.repository = repository;
    }

    public List<User> list() {
        return repository.findAll();
    }

    public User get(Long id) {
        return repository.findById(id).orElseThrow();
    }

    public User create(CreateUserRequest body) {
        return repository.save(new User(body.name()));
    }

    public void delete(Long id) {
        repository.deleteById(id);
    }
}
