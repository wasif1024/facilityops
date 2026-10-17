import { useState } from "react"
import { NavLink, Outlet, useNavigate } from "react-router"
import { Building2, LogOut, Menu, Moon, Sun } from "lucide-react"

import { roleLabel } from "@/api/labels"
import { authMode } from "@/auth/config"
import { useSession } from "@/auth/session"
import { IdentitySwitcher } from "@/components/app/identity-switcher"
import { useTheme } from "@/components/app/theme"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { cn } from "@/lib/utils"
import { visibleNavigation } from "@/navigation"

function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  const { canAny } = useSession()
  return (
    <nav aria-label="Main" className="space-y-6">
      {visibleNavigation(canAny).map((group) => (
        <div key={group.label}>
          <p className="text-muted-foreground mb-2 px-3 text-xs font-medium tracking-wide uppercase">{group.label}</p>
          <ul className="space-y-1">
            {group.items.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.to === "/requests"}
                  onClick={onNavigate}
                  className={({ isActive }) =>
                    cn(
                      "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                      isActive ? "bg-accent text-accent-foreground font-medium" : "hover:bg-accent/60",
                    )
                  }
                >
                  <item.icon aria-hidden className="size-4" />
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  )
}

function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme()
  const dark = resolvedTheme === "dark"
  return (
    <Button variant="ghost" size="icon" onClick={() => setTheme(dark ? "light" : "dark")} aria-label="Toggle dark mode">
      {dark ? <Sun /> : <Moon />}
    </Button>
  )
}

function Brand() {
  return (
    <div className="flex items-center gap-2 px-3 font-semibold">
      <Building2 aria-hidden className="text-primary size-5" />
      FacilityOps AI
    </div>
  )
}

export function AppShell() {
  const { session, signOut } = useSession()
  const navigate = useNavigate()
  // Back to the home page first, so the next person doesn't open on the previous user's page.
  const leave = () => {
    navigate("/", { replace: true })
    signOut()
  }
  const [menuOpen, setMenuOpen] = useState(false)

  return (
    <div className="bg-background min-h-svh md:grid md:grid-cols-[15rem_1fr]">
      <aside className="bg-sidebar hidden border-r p-4 md:block">
        <div className="mb-8">
          <Brand />
        </div>
        <SidebarNav />
      </aside>

      <div className="flex min-w-0 flex-col">
        <header className="bg-background/95 sticky top-0 z-10 flex h-14 items-center gap-2 border-b px-4 backdrop-blur">
          <Sheet open={menuOpen} onOpenChange={setMenuOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" className="md:hidden" aria-label="Open menu">
                <Menu />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-72 p-4">
              <SheetHeader className="mb-4 p-0">
                <SheetTitle asChild>
                  <div>
                    <Brand />
                  </div>
                </SheetTitle>
              </SheetHeader>
              <SidebarNav onNavigate={() => setMenuOpen(false)} />
            </SheetContent>
          </Sheet>
          <div className="md:hidden">
            <Brand />
          </div>

          <div className="ml-auto flex items-center gap-2">
            {session && (
              <div className="hidden items-center gap-1 lg:flex" aria-label="Your roles">
                {session.roles.map((role) => (
                  <Badge key={role} variant="secondary">
                    {roleLabel(role)}
                  </Badge>
                ))}
              </div>
            )}
            {authMode === "dev" && <IdentitySwitcher />}
            <Button variant="ghost" size="sm" onClick={leave}>
              <LogOut aria-hidden /> <span className="hidden sm:inline">Sign out</span>
              <span className="sr-only sm:hidden">Sign out</span>
            </Button>
            <ThemeToggle />
          </div>
        </header>

        <main className="mx-auto w-full max-w-6xl flex-1 p-4 md:p-8">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
