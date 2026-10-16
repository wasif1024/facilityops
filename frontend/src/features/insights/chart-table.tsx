/**
 * The table view behind a chart: the same numbers for screen readers, print, and anyone
 * who can't tell the series colours apart (some chart colours are below 3:1 contrast on
 * the light surface, so a table view is required alongside them).
 */
export function ChartTable({
  rows,
  columns,
  caption,
}: {
  rows: Record<string, number | string>[]
  columns: { key: string; label: string }[]
  caption: string
}) {
  return (
    <details className="mt-3 text-sm">
      <summary className="text-muted-foreground cursor-pointer select-none">View as table</summary>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-left">
          <caption className="sr-only">{caption}</caption>
          <thead>
            <tr className="border-b">
              {columns.map((c) => (
                <th key={c.key} scope="col" className="py-1 pr-4 font-medium">
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-b last:border-0">
                {columns.map((c) => (
                  <td key={c.key} className="py-1 pr-4 tabular-nums">
                    {row[c.key] ?? 0}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}
