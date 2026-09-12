import { httpBackend } from "./httpAdapter";
import type { OperationsBackend } from "./interfaces";

export const backend: OperationsBackend = httpBackend;
