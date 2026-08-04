import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { DeleteRecordingDialog } from "../src/recordings/DeleteRecordingDialog.tsx";

describe("DeleteRecordingDialog", () => {
  it("requires a clear permanent-deletion confirmation", async () => {
    const confirm = vi.fn();
    const cancel = vi.fn();
    const user = userEvent.setup();
    render(
      <DeleteRecordingDialog
        filename="private interview.m4a"
        open
        onCancel={cancel}
        onConfirm={confirm}
      />,
    );

    expect(screen.getByRole("dialog")).toHaveTextContent("private interview.m4a");
    expect(screen.getByRole("dialog")).toHaveTextContent("cannot be undone");
    expect(confirm).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Delete permanently" }));
    expect(confirm).toHaveBeenCalledOnce();
  });
});
