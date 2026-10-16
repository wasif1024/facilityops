import { useMutation, useQuery } from "@connectrpc/connect-query"
import { useEffect, useState } from "react"
import { Loader2, Search, UserPlus } from "lucide-react"
import { toast } from "sonner"

import { describeError } from "@/api/errors"
import { useInvalidateServices } from "@/api/invalidate"
import { roleLabel, userStatusLabels } from "@/api/labels"
import { Permission } from "@/auth/permissions"
import { useSession } from "@/auth/session"
import { StatusBadge } from "@/components/app/badges"
import { Pagination, useFilterParam, usePage } from "@/components/app/pagination"
import { EmptyState, ErrorState, ListSkeleton, PageHeader } from "@/components/app/states"
import { TimeAgo } from "@/components/app/time"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { type Role, type User, UserAdminService, UserStatus } from "@/gen/facilityops/v1/users_pb"

const ENTRA_ID_PATTERN = /^[A-Za-z0-9._-]{1,128}$/

function useRoles(): Role[] {
  const { data } = useQuery(UserAdminService.method.listRoles, {})
  return data?.roles ?? []
}

/** Administrators: who can sign in and what they may do (frontend spec 6.4). */
export function UsersPage() {
  const { can, session } = useSession()
  const page = usePage()
  const [role, setRole] = useFilterParam("role", "all")
  const [status, setStatus] = useFilterParam("status", "all")
  const [search, setSearch] = useFilterParam("q")
  const [draft, setDraft] = useState(search)
  const roles = useRoles()
  const [editing, setEditing] = useState<User | null>(null)
  const [creating, setCreating] = useState(false)

  // Search as you type, but don't send a request for every keystroke.
  useEffect(() => {
    const timer = window.setTimeout(() => draft !== search && setSearch(draft), 300)
    return () => window.clearTimeout(timer)
  }, [draft, search, setSearch])

  const { data, error, isLoading, refetch } = useQuery(UserAdminService.method.listUsers, {
    ...page,
    search,
    role: role === "all" ? "" : role,
    status: status === "active" ? UserStatus.ACTIVE : status === "disabled" ? UserStatus.DISABLED : UserStatus.UNSPECIFIED,
  })
  const manage = can(Permission.UsersManage)

  return (
    <div>
      <PageHeader
        title="Users"
        description="Everyone who has signed in or been added, and their roles. New sign-ins start as tenants."
        actions={
          manage && (
            <Button onClick={() => setCreating(true)}>
              <UserPlus aria-hidden /> Add user
            </Button>
          )
        }
      />
      <div className="mb-4 flex flex-wrap gap-2">
        <div className="relative w-full sm:w-64">
          <Search aria-hidden className="text-muted-foreground absolute top-2.5 left-2.5 size-4" />
          <Input
            aria-label="Search by name or email"
            placeholder="Search name or email"
            className="pl-8"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
        </div>
        <Select value={role} onValueChange={setRole}>
          <SelectTrigger className="w-44" aria-label="Filter by role">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All roles</SelectItem>
            {roles.map((r) => (
              <SelectItem key={r.id} value={r.id}>
                {r.displayName}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger className="w-36" aria-label="Filter by status">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Any status</SelectItem>
            <SelectItem value="active">Active</SelectItem>
            <SelectItem value="disabled">Disabled</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {isLoading ? (
        <ListSkeleton />
      ) : error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : !data?.users.length ? (
        <EmptyState title="No users match" />
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>User</TableHead>
                <TableHead>Roles</TableHead>
                <TableHead className="hidden md:table-cell">Status</TableHead>
                <TableHead className="hidden lg:table-cell">Last sign-in</TableHead>
                {manage && <TableHead className="text-right">Actions</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.users.map((user) => (
                <TableRow key={user.id}>
                  <TableCell>
                    <div className="font-medium">{user.displayName || user.externalId}</div>
                    <div className="text-muted-foreground text-xs">{user.email || user.externalId}</div>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {user.roles.length ? (
                        user.roles.map((r) => (
                          <Badge key={r} variant="secondary">
                            {roleLabel(r)}
                          </Badge>
                        ))
                      ) : (
                        <span className="text-muted-foreground text-sm">No roles</span>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="hidden md:table-cell">
                    <StatusBadge {...userStatusLabels[user.status]} />
                  </TableCell>
                  <TableCell className="hidden lg:table-cell">
                    <TimeAgo value={user.lastLoginAt} />
                  </TableCell>
                  {manage && (
                    <TableCell className="space-x-2 text-right whitespace-nowrap">
                      <Button size="sm" variant="outline" onClick={() => setEditing(user)}>
                        Edit roles
                      </Button>
                      {user.id !== session?.userId && <StatusToggle user={user} />}
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Pagination total={data.total} />
        </>
      )}
      {editing && <RolesDialog user={editing} roles={roles} onClose={() => setEditing(null)} />}
      {creating && <CreateUserDialog roles={roles} onClose={() => setCreating(false)} />}
    </div>
  )
}

function useUserMutation<M extends Parameters<typeof useMutation>[0]>(method: M, success: string, onDone?: () => void) {
  const invalidate = useInvalidateServices()
  return useMutation(method, {
    onSuccess: async () => {
      toast.success(success)
      await invalidate(UserAdminService)
      onDone?.()
    },
    onError: (error) => {
      const { title, message } = describeError(error)
      toast.error(title, { description: message })
    },
  })
}

function StatusToggle({ user }: { user: User }) {
  const disable = user.status === UserStatus.ACTIVE
  const name = user.displayName || user.externalId
  const update = useUserMutation(UserAdminService.method.updateUser, disable ? `${name} disabled` : `${name} enabled`)
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button size="sm" variant={disable ? "ghost" : "outline"}>
          {disable ? "Disable" : "Enable"}
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {disable ? "Disable" : "Enable"} {name}?
          </AlertDialogTitle>
          <AlertDialogDescription>
            {disable
              ? "They will be refused on their next request. Their history is kept."
              : "They will be able to sign in again with their current roles."}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={() =>
              update.mutate({ userId: user.id, status: disable ? UserStatus.DISABLED : UserStatus.ACTIVE })
            }
          >
            {disable ? "Disable" : "Enable"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

function RoleCheckboxes({
  roles,
  selected,
  onChange,
}: {
  roles: Role[]
  selected: string[]
  onChange: (roles: string[]) => void
}) {
  return (
    <fieldset className="space-y-3">
      <legend className="mb-2 text-sm font-medium">Roles</legend>
      {roles.map((role) => (
        <div key={role.id} className="flex items-start gap-3">
          <Checkbox
            id={`role-${role.id}`}
            checked={selected.includes(role.id)}
            onCheckedChange={(checked) =>
              onChange(checked ? [...selected, role.id] : selected.filter((r) => r !== role.id))
            }
          />
          <div className="grid gap-0.5">
            <Tooltip>
              <TooltipTrigger asChild>
                <Label htmlFor={`role-${role.id}`} className="w-fit">
                  {role.displayName}
                </Label>
              </TooltipTrigger>
              <TooltipContent className="max-w-xs">{role.permissions.join(", ")}</TooltipContent>
            </Tooltip>
            <p className="text-muted-foreground text-xs">{role.description}</p>
          </div>
        </div>
      ))}
    </fieldset>
  )
}

function RolesDialog({ user, roles, onClose }: { user: User; roles: Role[]; onClose: () => void }) {
  const [selected, setSelected] = useState(user.roles)
  const save = useUserMutation(UserAdminService.method.setUserRoles, "Roles updated", onClose)
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Roles for {user.displayName || user.externalId}</DialogTitle>
          <DialogDescription>Hover a role to see the permissions it grants. Changes are audited.</DialogDescription>
        </DialogHeader>
        <RoleCheckboxes roles={roles} selected={selected} onChange={setSelected} />
        <DialogFooter>
          <Button disabled={save.isPending} onClick={() => save.mutate({ userId: user.id, roles: selected })}>
            {save.isPending && <Loader2 aria-hidden className="animate-spin" />} Save roles
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function CreateUserDialog({ roles, onClose }: { roles: Role[]; onClose: () => void }) {
  const [externalId, setExternalId] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [email, setEmail] = useState("")
  const [selected, setSelected] = useState<string[]>([])
  const create = useUserMutation(UserAdminService.method.createUser, "User added", onClose)
  const validId = ENTRA_ID_PATTERN.test(externalId)

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add a user</DialogTitle>
          <DialogDescription>
            Pre-provision staff before their first sign-in. Use their Entra object id (oid) from the Entra admin center.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            create.mutate({ externalId, displayName, email, roles: selected })
          }}
        >
          <div className="space-y-2">
            <Label htmlFor="oid">Entra object id</Label>
            <Input
              id="oid"
              value={externalId}
              onChange={(event) => setExternalId(event.target.value.trim())}
              aria-invalid={externalId !== "" && !validId}
              required
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="name">Display name</Label>
              <Input id="name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input id="email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} />
            </div>
          </div>
          <RoleCheckboxes roles={roles} selected={selected} onChange={setSelected} />
          <DialogFooter>
            <Button type="submit" disabled={!validId || create.isPending}>
              {create.isPending && <Loader2 aria-hidden className="animate-spin" />} Add user
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
