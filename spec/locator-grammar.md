# OkNoll bundle locator grammar v1

Status: **stable** (v1)
Test vectors: `fixtures/locators/vectors.json` — the parser must pass them
verbatim.

Every command that consumes knowledge accepts one locator grammar:

```text
./bundle                                    unmanaged local OKF directory/archive
file:./bundle                               same, explicit scheme
local:handbook                              installed image / catalog alias
oci://ghcr.io/acme/handbook:1.0             image in an OCI registry
oci://registry.example.com/acme/handbook@sha256:<64-hex>
oknoll://openknoll.com/acme/handbook:stable bundle on an OpenKnoll-compatible host
oknoll://openknoll.com/acme/handbook@sha256:<64-hex>
```

## EBNF

```ebnf
locator        = file-locator | local-locator | oci-locator
               | oknoll-locator | path-locator | bare-name ;

(* --- schemes ------------------------------------------------------- *)
file-locator   = "file:" , path ;
local-locator  = "local:" , alias , [ ":" , tag ] ;
oci-locator    = "oci://" , host , "/" , repo-path , [ reference ] ;
oknoll-locator = "oknoll://" , host , "/" , namespace , "/" , name ,
                 [ reference ] ;

(* --- path form (no scheme) ----------------------------------------- *)
(* A token is a path when it starts with "/", "./", "../", or "~/",     *)
(* or contains a "/" anywhere while carrying no scheme prefix.          *)
path-locator   = path ;

(* --- interactive convenience ---------------------------------------- *)
(* A bare alias with no scheme and no "/" resolves to a local catalog   *)
(* alias in interactive contexts ONLY. Machine-readable input/output    *)
(* must always use an explicit scheme; citations are fully qualified.   *)
bare-name      = alias ;

(* --- components ----------------------------------------------------- *)
reference      = ":" , tag | "@" , digest ;      (* mutually exclusive *)
digest         = "sha256:" , 64 * hex-lower ;
tag            = tag-char , { tag-char } ;        (* max 128 chars *)
tag-char       = alnum | "." | "_" | "-" ;
alias          = lower-digit , { lower-digit | "." | "_" | "-" } ;
namespace      = alias ;
name           = alias ;
repo-path      = name , { "/" , name } ;
host           = hostname , [ ":" , port ] ;
hostname       = (* RFC 1123 host: labels of alnum/hyphen joined by "." *) ;
lower-digit    = "a" | … | "z" | "0" | … | "9" ;
hex-lower      = "0" | … | "9" | "a" | … | "f" ;
```

## Rules

1. **No bare hashes.** After `@`, the only accepted form is
   `sha256:` + exactly 64 lowercase hex characters, and it always denotes the
   **OCI manifest digest**. OKF revisions are only ever spelled `rev-…` and are
   never valid in a locator reference. Any other `@…` is a parse error.
2. **Tag and digest are mutually exclusive.** `name:tag@sha256:…` is rejected
   as ambiguous rather than silently preferring the digest.
3. **Bare names are interactive-only.** `handbook` may resolve to a catalog
   alias when a person types it. Parsers expose it as a distinct result kind;
   machine contexts (config files, API payloads, stored references, citations)
   must reject it and require an explicit scheme.
4. **Aliases are a flat lowercase namespace**: `[a-z0-9][a-z0-9._-]*`.
   Uppercase anywhere in an alias, namespace, or repository name is a parse
   error (hosts are case-insensitive and normalized to lowercase).
5. **`cloud:` is not a scheme.** The historical `cloud:<bundle-id>` placeholder
   is rejected with guidance toward `oknoll://`.
6. **Empty components are errors**: `oci://host`, `oknoll://host/name` (missing
   namespace), trailing `/`, empty tag after `:`, or an empty locator.

## Result kinds

A conforming parser returns one of:

```text
path        { path }                          unmanaged directory or archive
local       { name, tag? }                    catalog alias / installed image
oci         { host, repository, tag?, digest? }
oknoll      { host, namespace, name, tag?, digest? }
bare        { name }                          interactive contexts only
```
