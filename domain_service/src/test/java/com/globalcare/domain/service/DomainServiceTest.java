package com.globalcare.domain.service;

import com.globalcare.domain.model.RepairStatusView;
import org.junit.jupiter.api.Test;


import static org.junit.jupiter.api.Assertions.assertEquals;

class DomainServiceTest {

    @Test
    void shouldReturnRepairStatusForMockOrder() {
        DomainService service = new DomainService();
        RepairStatusView status = service.getRepairStatus("ORD-MY-2001", null);

        assertEquals("MY", status.market());
        assertEquals("IN_REPAIR", status.status());
        assertEquals("维修处理中", status.statusLabel());
    }

}
