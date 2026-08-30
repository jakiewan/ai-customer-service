package com.globalcare.domain.model;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;

import java.util.List;
import java.util.Map;

public record CaseDraftRequest(
        @NotBlank String conversationId,
        @NotBlank String source,
        @NotBlank String draftFingerprint,
        @NotBlank String market,
        @NotBlank String language,
        @NotBlank String issueCategory,
        @NotBlank String issueSummary,
        @NotBlank String suggestedResolution,
        String orderId,
        String serialNumber,
        String productModel,
        @NotEmpty Map<String, Object> customerContext,
        @NotEmpty List<Map<String, Object>> policyEvidence,
        @NotEmpty List<String> toolTraceIds,
        String createdAt
) {
}
