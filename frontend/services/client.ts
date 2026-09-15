import { httpBackend } from "./httpAdapter";
import type { OperationsBackend } from "./interfaces";

export function createBackend(kind: "http" | "mock", mock?: OperationsBackend): OperationsBackend {
  if (kind === "mock") {
    if (!mock) throw new Error("mock backend is required when kind is 'mock'");
    return mock;
  }
  return httpBackend;
}

export const backend: OperationsBackend = httpBackend;
