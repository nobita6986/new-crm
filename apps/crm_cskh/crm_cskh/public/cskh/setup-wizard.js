(() => {
	const welcomeSlide = frappe.setup.slides_settings.find((slide) => slide.name === "welcome");
	const loadWelcome = welcomeSlide.onload;
	const initializeFields = welcomeSlide.initialize_fields;

	welcomeSlide.onload = function (slide) {
		// Keep the control available to Frappe for regional defaults and form submission.
		slide.get_field("country").$wrapper.hide();
		return loadWelcome.call(this, slide);
	};

	welcomeSlide.initialize_fields = function (slide) {
		const values = frappe.wizard.values;
		values.country ||= "Vietnam";

		// After a language change, Frappe initializes fields from cached regional data.
		const region = frappe.setup.data.regional_data?.country_info[values.country];
		values.currency ||= region?.currency;
		values.timezone ||= region?.timezones?.[0];

		return initializeFields.call(this, slide);
	};
})();
