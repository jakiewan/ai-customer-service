package com.globalcare.domain.service;

import com.globalcare.domain.model.CaseDraftRequest;
import com.globalcare.domain.model.CaseDraftResponse;
import com.globalcare.domain.model.RepairStatusView;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class DomainServiceTest {

    @Test
    void shouldReturnRepairStatusForMockOrder() {
        DomainService service = new DomainService();
        RepairStatusView status = service.getRepairStatus("ORD-MY-2001", null);

        assertEquals("MY", status.market());
        assertEquals("IN_REPAIR", status.status());
        assertEquals("维修处理中", status.statusLabel());
    }

    @Test
    void shouldReuseCaseDraftByFingerprint() {
        DomainService service = new DomainService();
        CaseDraftRequest request = request("same-fingerprint");

        CaseDraftResponse first = service.createCaseDraft(request);
        CaseDraftResponse second = service.createCaseDraft(request);

        assertEquals(first.caseDraftId(), second.caseDraftId());
        assertTrue(second.idempotentReplay());
    }

    @Test
    void shouldExposeSpecificCodeForMissingCaseDraft() {
        DomainService service = new DomainService();

        DomainNotFoundException exception = assertThrows(
                DomainNotFoundException.class,
                () -> service.getCaseDraft("missing-draft")
        );

        assertEquals("CASE_DRAFT_NOT_FOUND", exception.code());
    }

    private CaseDraftRequest request(String fingerprint) {
        return new CaseDraftRequest(
                "00000000-0000-0000-0000-000000000001",
                "AI_AGENT",
                fingerprint,
                "MY",
                "zh-CN",
                "cannot_power_on",
                "设备无法开机",
                "repair",
                "ORD-MY-2001",
                "SN-MY-2001",
                "Xiaomi 15",
                Map.of("customer_id", "CUS-MY-001"),
                List.of(Map.of("policy_id", "MY-MOBILE-AFTERSALES-2026")),
                List.of("trace-001"),
                "2026-08-30T00:00:00Z"
        );
    }
}
