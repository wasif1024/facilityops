import { useState } from "react"
import { Check, UserRoundCog } from "lucide-react"

import { DEV_USER_PATTERN, SUGGESTED_DEV_USERS, loadRecentDevUsers } from "@/auth/config"
import { useIdentity } from "@/auth/session"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"

/**
 * Development only: choose who you are (sent as X-Dev-User). The badge stays visible so
 * screenshots can't be mistaken for production. Excluded from Entra builds (spec 4.1).
 */
export function IdentitySwitcher() {
  const { devUser, switchDevUser } = useIdentity()
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState("")
  const valid = DEV_USER_PATTERN.test(draft)
  const options = [...new Set([...loadRecentDevUsers(), ...SUGGESTED_DEV_USERS])]

  const choose = (user: string) => {
    switchDevUser(user)
    setDraft("")
    setOpen(false)
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className="gap-2">
          <UserRoundCog aria-hidden />
          <span className="max-w-32 truncate">{devUser}</span>
          <Badge variant="secondary" className="hidden sm:inline-flex">
            Development identity
          </Badge>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72 space-y-3">
        <div>
          <p className="text-sm font-medium">Switch identity</p>
          <p className="text-muted-foreground text-xs">
            Local development only. New names start as tenants; grant roles with{" "}
            <code>python -m backend.app.cli grant-role</code>.
          </p>
        </div>
        <ul className="space-y-1">
          {options.map((user) => (
            <li key={user}>
              <Button variant="ghost" size="sm" className="w-full justify-between" onClick={() => choose(user)}>
                {user}
                {user === devUser && <Check aria-hidden />}
              </Button>
            </li>
          ))}
        </ul>
        <form
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            if (valid) choose(draft)
          }}
        >
          <Input
            aria-label="Another identity"
            placeholder="another-name"
            value={draft}
            onChange={(event) => setDraft(event.target.value.toLowerCase())}
            aria-invalid={draft !== "" && !valid}
          />
          <Button type="submit" size="sm" disabled={!valid}>
            Use
          </Button>
        </form>
      </PopoverContent>
    </Popover>
  )
}
