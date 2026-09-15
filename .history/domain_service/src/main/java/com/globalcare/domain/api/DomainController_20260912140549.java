package com.globalcare.domain.api;

import com.globalcare.domain.model.CaseDraftRequest;
import com.globalcare.domain.model.CaseDraftResponse;
import com.globalcare.domain.model.DeviceView;
import com.globalcare.domain.model.OrderView;
import com.globalcare.domain.model.RepairStatusView;
import com.globalcare.domain.model.ServiceCenterView;
import com.globalcare.domain.service.DomainService;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/internal")
public class DomainController {

    private final DomainService domainService;

    public DomainController(DomainService domainService) {
        this.domainService = domainService;
    }

    @RequestMapping(value = "/orders/{orderId}", method = RequestMethod.GET)
    public OrderView getOrder(@PathVariable String orderId) {
        return domainService.getOrder(orderId);
    }

    @RequestMapping(value = "/devices/{serialNumber}", method = RequestMethod.GET)
    public DeviceView getDevice(@PathVariable String serialNumber) {
        return domainService.getDevice(serialNumber);
    }

    @RequestMapping(value = "/repair-cases/status", method = RequestMethod.GET)
    public RepairStatusView getRepairStatus(
            @RequestParam(name = "order_id", required = false) String orderId,
            @RequestParam(name = "serial_number", required = false) String serialNumber
    ) {
        return domainService.getRepairStatus(orderId, serialNumber);
    }

    @RequestMapping(value = "/repair-cases/{caseId}", method = RequestMethod.GET)
    public CaseDraftResponse getRepairCase(@PathVariable String caseId) {
        return domainService.getCaseDraft(caseId);
    }

    @RequestMapping(value = "/service-centers", method = RequestMethod.GET)
    public List<ServiceCenterView> getServiceCenters(@RequestParam String market) {
        return domainService.getServiceCenters(market);
    }

    @RequestMapping(value = "/case-drafts", method = RequestMethod.POST)
    public CaseDraftResponse createCaseDraft(@Valid @RequestBody CaseDraftRequest request) {
        return domainService.createCaseDraft(request);
    }

    @RequestMapping(value = "/case-drafts/{caseDraftId}", method = RequestMethod.GET)
    public CaseDraftResponse getCaseDraft(@PathVariable String caseDraftId) {
        return domainService.getCaseDraft(caseDraftId);
    }
}
