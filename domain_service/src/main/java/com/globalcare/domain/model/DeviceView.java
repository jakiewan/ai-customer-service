package com.globalcare.domain.model;

public record DeviceView(
        String serialNumber,
        String orderId,
        String customerId,
        String market,
        String productModel,
        String purchaseDate,
        String repairStatus
) {
}
