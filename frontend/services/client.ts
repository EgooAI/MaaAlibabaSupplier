import { httpBackend } from "./httpAdapter";
import type { OperationsBackend } from "./interfaces";

/** Backend injection seam: tests mock this module to replace the whole API surface. */
export const backend: OperationsBackend = httpBackend;
