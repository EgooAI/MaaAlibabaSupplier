import { httpBackend } from "./httpAdapter";
import type { OperationsBackend } from "./interfaces";
import { mockBackend } from "./mockAdapter";

export const backend: OperationsBackend = process.env.NEXT_PUBLIC_USE_HTTP_BACKEND === "true" ? httpBackend : mockBackend;
