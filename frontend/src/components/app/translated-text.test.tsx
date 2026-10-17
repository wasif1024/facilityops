import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { TranslatedText } from "@/components/app/translated-text"

describe("TranslatedText (FR-60)", () => {
  it("shows the labelled machine translation first and the original on request", async () => {
    render(
      <TranslatedText
        text="Bonjour, je suis [PII_0001]. Le radiateur ne chauffe plus."
        translated="Hello, I'm [PII_0001]. The radiator isn't heating."
        sourceLanguage="fr"
      />,
    )
    expect(screen.getByText(/The radiator isn't heating/)).toBeInTheDocument()
    expect(screen.getByText(/Machine translation from French/)).toBeInTheDocument()

    await userEvent.setup().click(screen.getByRole("button", { name: "Show original" }))
    expect(screen.getByText(/Le radiateur ne chauffe plus/)).toBeInTheDocument()
    expect(screen.getByText(/Original \(French\)/)).toBeInTheDocument()
  })

  it("applies revealed values to the translation's placeholders", () => {
    render(
      <TranslatedText
        text="Je suis [PII_0001]."
        translated="I'm [PII_0001]."
        sourceLanguage="fr"
        revealed={{ "[PII_0001]": "Marie Dubois" }}
      />,
    )
    expect(screen.getByText("Marie Dubois")).toBeInTheDocument()
  })

  it("shows the original alone when there is no translation", () => {
    render(<TranslatedText text="The kitchen light flickers." />)
    expect(screen.getByText("The kitchen light flickers.")).toBeInTheDocument()
    expect(screen.queryByText(/Machine translation/)).not.toBeInTheDocument()
  })
})
