import { UserRepository } from "./repository";

export class BaseService {
  describe() {
    return "base";
  }
}

export class AuthService extends BaseService {
  login(name: string) {
    return this.describe() + this.lookup(name);
  }

  private lookup(name: string) {
    return name;
  }
}

export function buildRepository() {
  return new UserRepository();
}
