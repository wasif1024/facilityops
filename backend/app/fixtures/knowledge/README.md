# Synthetic knowledge

Invented equipment manuals and safety procedures for the incident team's knowledge base (Foundry IQ on Azure AI
Search). No real manufacturer, product or procedure is described. Each file starts with a metadata block; every
`##` section becomes one searchable passage, cited as `<id>` + section.

`notes/` holds deliberately untrusted content, including an instruction hidden in a "community tip", so the
evals can prove that retrieved text is screened by Prompt Shields before any agent reads it.
