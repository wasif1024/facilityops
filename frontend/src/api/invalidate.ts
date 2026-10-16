import type { DescService } from "@bufbuild/protobuf"
import { createConnectQueryKey } from "@connectrpc/connect-query"
import { useQueryClient } from "@tanstack/react-query"
import { useCallback } from "react"

/**
 * After a change, refresh every cached query of the affected services, e.g. resolving a
 * review case updates the queue, the request list and the work orders.
 */
export function useInvalidateServices() {
  const queryClient = useQueryClient()
  return useCallback(
    (...services: DescService[]) =>
      Promise.all(
        services.map((schema) =>
          queryClient.invalidateQueries({ queryKey: createConnectQueryKey({ schema, cardinality: undefined }) }),
        ),
      ),
    [queryClient],
  )
}
