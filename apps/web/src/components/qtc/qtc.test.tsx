// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@/i18n/I18nProvider";
import { dictionaries } from "@/i18n/dictionaries";
import { ApiError } from "@/lib/api";
import { CustomerForm, customerBody, emptyDraft } from "./CustomerForm";
import { QtcError } from "./QtcShell";

vi.mock("next/navigation", () => ({ usePathname: () => "/qtc/customers", useRouter: () => ({ push: vi.fn() }) }));

const th = dictionaries.th;
afterEach(cleanup);
const wrap = (ui: React.ReactNode) => render(<I18nProvider>{ui}</I18nProvider>);

describe("CustomerForm", () => {
  it("checks the Thai tax ID before sending and blocks saving while it is wrong", () => {
    const onSubmit = vi.fn(async () => {});
    wrap(<CustomerForm initial={emptyDraft} creating onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText(th["qtc.customer.name"]), { target: { value: "ABC" } });
    fireEvent.change(screen.getByLabelText(th["qtc.customer.taxId"]), { target: { value: "0105536000314" } });
    expect(screen.getByRole("alert").textContent).toBe(th["qtc.customer.taxIdInvalid"]);
    expect((screen.getByRole("button", { name: th["qtc.save"] }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(th["qtc.customer.taxId"]), { target: { value: "0105536000313" } });
    expect((screen.getByRole("button", { name: th["qtc.save"] }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("sends the customer, leaving the code out so the server numbers it", async () => {
    const onSubmit = vi.fn(async () => {});
    wrap(<CustomerForm initial={emptyDraft} creating onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText(th["qtc.customer.name"]), { target: { value: "  ABC Studio " } });
    fireEvent.click(screen.getByRole("button", { name: th["qtc.save"] }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const body = (onSubmit.mock.calls[0] as unknown as [Record<string, unknown>])[0];
    expect(body).toMatchObject({ name: "ABC Studio", tax_id: null, branch: "00000", payment_terms_days: 30 });
    expect("code" in body).toBe(false);
  });

  it("shows the server's refusal inside the form", async () => {
    const onSubmit = vi.fn(async () => {
      throw new ApiError(409, "CONFLICT", "Customer code C0001 is already used");
    });
    wrap(<CustomerForm initial={{ ...emptyDraft, name: "X", code: "C0001" }} creating onSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole("button", { name: th["qtc.save"] }));
    expect(await screen.findByText(/C0001 is already used/)).toBeTruthy();
  });

  it("clears the tax ID explicitly when editing", () => {
    expect(customerBody({ ...emptyDraft, name: "X", code: "C0009" }, { creating: false })).toMatchObject({ code: "C0009", tax_id: null });
  });
});

describe("QtcError", () => {
  it("shows a clear permission-denied state for 403", () => {
    wrap(<QtcError error={new ApiError(403, "FORBIDDEN", "Owner or admin role required")} />);
    expect(screen.getByRole("alert").textContent).toContain(th["qtc.denied"]);
    expect(screen.getByRole("alert").textContent).toContain("Owner or admin role required");
  });

  it("uses the normal error state (with retry) for other failures", () => {
    const retry = vi.fn();
    wrap(<QtcError error={new ApiError(0, "NETWORK_ERROR", "down")} onRetry={retry} />);
    expect(screen.queryByText(th["qtc.denied"])).toBeNull();
    fireEvent.click(screen.getByRole("button"));
    expect(retry).toHaveBeenCalled();
  });
});
