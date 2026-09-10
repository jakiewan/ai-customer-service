package com.globalcare.domain.service;

public class DomainNotFoundException extends RuntimeException {

    private final String code;

    public DomainNotFoundException(String message) {
        this(message, "NOT_FOUND");
    }

    public DomainNotFoundException(String message, String code) {
        super(message);
        this.code = code;
    }

    public String code() {
        return code;
    }
}
