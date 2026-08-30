package com.globalcare.domain.model;

public record OrderView(
        String orderId,
        String customerId,
        String market,
        String productModel,
        String serialNumber,
        String purchaseDate,
        String repairStatus
) {
}
