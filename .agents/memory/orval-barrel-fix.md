---
name: Orval Barrel Collision Fix
description: Fix for the barrel re-export collision when running Orval codegen in this workspace
---

# Orval Barrel Collision Fix

## Problem
Orval generates a barrel `index.ts` that re-exports from both `./generated/api` and `./generated/types`. When both exist, TypeScript sees duplicate exports and codegen fails with "re-export collision".

## Fix Applied
1. Removed `schemas: { path: "generated/types", type: "typescript" }` from `lib/api-spec/orval.config.ts`
2. Added a post-codegen sed step in `lib/api-spec/package.json` codegen command to strip the `./generated/types` re-export line from the generated barrel
3. `lib/api-zod/src/index.ts` only exports from `./generated/api`

**Why:** Orval's schema generator creates a second barrel entry that conflicts with the main generated output when the schemas directory path differs from the api path.

**How to apply:** If you add a new Orval target, do NOT add a `schemas` output config unless you explicitly manage the barrel yourself.
