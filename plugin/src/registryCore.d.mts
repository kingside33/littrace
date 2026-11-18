/** Types for registryCore.mjs; the implementation is plain ESM on purpose. */

export type Disposer = () => void;

export type ErrorReporter = (error: unknown, id: string) => void;

export interface Registry {
  register(win: object, id: string, dispose: Disposer): void;
  registerGlobal(id: string, dispose: Disposer): void;
  claimWindow(win: object): boolean;
  unregisterWindow(win: object): void;
  unregisterGlobals(): void;
  unregisterEverything(): void;
}

export function createRegistry(onError?: ErrorReporter): Registry;
