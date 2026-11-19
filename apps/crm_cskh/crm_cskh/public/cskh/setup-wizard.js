(() => {
	if (!window.frappe || !frappe.setup || !Array.isArray(frappe.setup.slides_settings)) {
		return;
	}
	const welcomeSlide = frappe.setup.slides_settings.find((slide) => slide.name === "welcome");
	if (!welcomeSlide) {
		return;
	}
	const loadWelcome = welcomeSlide.onload;
	const initializeFields = welcomeSlide.initialize_fields;

	welcomeSlide.onload = function (slide) {
		// Keep the control available to Frappe for regional defaults and form submission.
		const countryField = slide && typeof slide.get_field === "function" ? slide.get_field("country") : null;
		if (countryField && countryField.$wrapper && typeof countryField.$wrapper.hide === "function") {
			countryField.$wrapper.hide();
		}
		return typeof loadWelcome === "function" ? loadWelcome.call(this, slide) : undefined;
	};

	welcomeSlide.initialize_fields = function (slide) {
		const values = frappe.wizard && frappe.wizard.values ? frappe.wizard.values : null;
		if (values) {
			values.country ||= "Vietnam";

			// After a language change, Frappe initializes fields from cached regional data.
			const region = frappe.setup.data?.regional_data?.country_info?.[values.country];
			values.currency ||= region?.currency;
			values.timezone ||= region?.timezones?.[0];
		}

		return typeof initializeFields === "function" ? initializeFields.call(this, slide) : undefined;
	};
})();
