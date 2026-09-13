/** Component tests for the presentation primitives.
 *
 *  Run with `npm test` (requires Node.js). These cover the label and tone
 *  mapping that the whole UI leans on — if `PARTIAL` ever renders as green or
 *  as the raw enum name, the product is actively misleading.
 */
import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  ConditionList,
  CoverageBadge,
  Metric,
  RiskBadge,
  coverageLabel,
  coverageTone,
  formatDate,
  titleize,
} from "./ui";

describe("coverage labelling", () => {
  it("renders human labels, never raw enum values", () => {
    expect(coverageLabel("COVERED")).toBe("Covered");
    expect(coverageLabel("PARTIAL")).toBe("Partially Covered");
    expect(coverageLabel("NOT_COVERED")).toBe("Not Covered");
    expect(coverageLabel("INSUFFICIENT_EVIDENCE")).toBe("Insufficient Evidence");
  });

  it("falls back to a neutral label when nothing was analysed", () => {
    expect(coverageLabel(null)).toBe("Not analysed");
    expect(coverageTone(null)).toBe("neutral");
  });

  it("maps each verdict to a distinct tone", () => {
    expect(coverageTone("COVERED")).toBe("covered");
    expect(coverageTone("PARTIAL")).toBe("partial");
    expect(coverageTone("NOT_COVERED")).toBe("missing");
  });
});

describe("CoverageBadge", () => {
  it("shows the label and the matching tone class", () => {
    const { container } = render(<CoverageBadge coverage="PARTIAL" />);
    expect(screen.getByText("Partially Covered")).toBeTruthy();
    expect(container.querySelector(".badge--partial")).toBeTruthy();
  });
});

describe("RiskBadge", () => {
  it("renders nothing when risk is unknown", () => {
    const { container } = render(<RiskBadge risk={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders high risk with the missing tone", () => {
    const { container } = render(<RiskBadge risk="HIGH" />);
    expect(container.querySelector(".badge--missing")).toBeTruthy();
    expect(screen.getByText(/High risk/)).toBeTruthy();
  });
});

describe("ConditionList", () => {
  it("renders each condition as a key = value pair", () => {
    render(<ConditionList conditions={{ discount: "100%", currency: "EUR" }} />);
    expect(screen.getByText("discount = 100%")).toBeTruthy();
    expect(screen.getByText("currency = EUR")).toBeTruthy();
  });

  it("says so explicitly when nothing was extracted", () => {
    render(<ConditionList conditions={{}} />);
    expect(screen.getByText("none extracted")).toBeTruthy();
  });
});

describe("Metric", () => {
  it("renders label, value and hint", () => {
    render(<Metric label="Test gaps" value={23} hint="across 7 features" />);
    expect(screen.getByText("Test gaps")).toBeTruthy();
    expect(screen.getByText("23")).toBeTruthy();
    expect(screen.getByText("across 7 features")).toBeTruthy();
  });
});

describe("formatting helpers", () => {
  it("titleizes enum-style names", () => {
    expect(titleize("BOUNDARY_CONDITION")).toBe("Boundary Condition");
    expect(titleize("")).toBe("");
  });

  it("never renders an invalid date as text", () => {
    expect(formatDate(null)).toBe("—");
    expect(formatDate("not a date")).toBe("—");
  });
});
