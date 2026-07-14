# ToonFlow Foundation — ADR-006: Storage & Blob

## Status
Approved (Foundation)

## Context
Artifact content, resource thumbnails, media assets, and provider results need durable, versioned storage.

## Decisions
1. **Blob Storage**: Local filesystem for Foundation (path configurable); S3-compatible adapter for production
2. **BlobRef**: `storage_provider://bucket/prefix/sha256.ext` — content-addressed, immutable reference
3. **Durability Barrier**: Blob write confirmed before ArtifactVersion record created
4. **Signed URLs**: Short-lived (5 min default) for private blobs; no public permanent URLs
5. **Upload Session**: Multipart upload with idempotency key; resume on failure
6. **Lifecycle**: Blobs referenced by active Revision kept; orphan blobs cleaned after configurable TTL
7. **Content Hash**: SHA-256 computed server-side; client-provided hash verified if available

## Consequences
- Blob metadata (size, hash, mime) stored in DB; content in storage backend
- Signed URL access is audited per request
- Migration to S3 requires only a storage adapter change — no data model changes
