import "@testing-library/jest-dom/vitest";
import "fake-indexeddb/auto";

import { expect } from "vitest";
import { toHaveNoViolations } from "jest-axe";

expect.extend(toHaveNoViolations);
