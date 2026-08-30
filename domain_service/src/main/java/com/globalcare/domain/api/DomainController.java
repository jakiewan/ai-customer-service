package com.globalcare.domain.api;

import com.globalcare.domain.model.CaseDraftRequest;
import com.globalcare.domain.model.CaseDraftResponse;
import com.globalcare.domain.model.DeviceView;
import com.globalcare.domain.model.OrderView;
import com.globalcare.domain.model.RepairStatusView;
import com.globalcare.domain.model.ServiceCenterView;
import com.globalcare.domain.service.DomainService;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/internal")
public class DomainController {

    private final DomainService domainService;

    public DomainController(DomainService domainService) {
        this.domainService = domainService;
    }

    @GetMapping("/orders/{orderId}")
    public OrderView getOrder(@PathVariable String orderId) {
        return domainService.getOrder(orderId);
    }

    @GetMapping("/devices/{serialNumber}")
    public DeviceView getDevice(@PathVariable String serialNumber) {
        return domainService.getDevice(serialNumber);
    }

    @GetMapping("/repair-cases/status")
    public RepairStatusView getRepairStatus(
            @RequestParam(name = "order_id", required = false) String orderId,
            @RequestParam(name = "serial_number", required = false) String serialNumber
    ) {
        return domainService.getRepairStatus(orderId, serialNumber);
    }

    @GetMapping("/repair-cases/{caseId}")
    public CaseDraftResponse getRepairCase(@PathVariable String caseId) {
        return domainService.getCaseDraft(caseId);
    }

    @GetMapping("/service-centers")
    public List<ServiceCenterView> getServiceCenters(@RequestParam String market) {
        return domainService.getServiceCenters(market);
    }

    @PostMapping("/case-drafts")
    public CaseDraftResponse createCaseDraft(@Valid @RequestBody CaseDraftRequest request) {
        return domainService.createCaseDraft(request);
    }

    @GetMapping("/case-drafts/{caseDraftId}")
    public CaseDraftResponse getCaseDraft(@PathVariable String caseDraftId) {
        return domainService.getCaseDraft(caseDraftId);
    }
}
