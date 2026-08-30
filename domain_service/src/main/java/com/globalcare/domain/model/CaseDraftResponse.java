package com.globalcare.domain.model;

import java.time.OffsetDateTime;
import java.util.Map;

public record CaseDraftResponse(
        String caseDraftId,
        String status,
        String conversationId,
        String draftFingerprint,
        boolean idempotentReplay,
        OffsetDateTime createdAt,
        Map<String, Object> payload
) {
}
