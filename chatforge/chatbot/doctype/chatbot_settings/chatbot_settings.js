// Copyright (c) 2025, finbyz and contributors
// For license information, please see license.txt

frappe.ui.form.on("Chatbot Settings", {
    refresh(frm) {
        if (!frm.is_new()) {
            // ========== FETCH SITEMAP BUTTON ==========
            frm.add_custom_button(__('Fetch Sitemap URLs'), function () {
                let d = new frappe.ui.Dialog({
                    title: __('Fetch Sitemap URLs'),
                    fields: [
                        {
                            label: __('Sitemap URL'),
                            fieldname: 'sitemap_url',
                            fieldtype: 'Data',
                            reqd: 1,
                            default: frm.doc.sitemap_url || '',
                            description: __('Enter the full URL to your sitemap.xml file (e.g., https://example.com/sitemap.xml)')
                        }
                    ],
                    size: 'large',
                    primary_action_label: __('Fetch'),
                    primary_action(values) {
                        d.hide();
                        frappe.show_alert({
                            message: __('🗺️ Fetching sitemap URLs... Please wait.'),
                            indicator: 'blue'
                        }, 10);

                        frappe.call({
                            method: 'chatforge.chatbot.doctype.chatbot_settings.chatbot_settings.fetch_sitemap_urls',
                            args: {
                                docname: frm.doc.name,
                                sitemap_url: values.sitemap_url
                            },
                            callback: function (r) {
                                if (r.message && r.message.success) {
                                    frappe.show_alert({
                                        message: r.message.message,
                                        indicator: 'green'
                                    }, 10);
                                    frm.reload_doc();
                                } else if (r.message && r.message.error) {
                                    frappe.msgprint({
                                        title: __('Error'),
                                        indicator: 'red',
                                        message: r.message.error
                                    });
                                }
                            },
                            error: function () {
                                frappe.msgprint({
                                    title: __('Error'),
                                    indicator: 'red',
                                    message: __('Failed to fetch sitemap. Please check the Error Log.')
                                });
                            }
                        });
                    }
                });
                d.show();
            }, __('Sitemap'));

            // ========== PROCESS SELECTED BUTTON ==========
            frm.add_custom_button(__('Process Selected URLs'), function () {
                // Check if any URLs are selected
                let selected_count = (frm.doc.sitemap_urls || []).filter(row => row.is_selected && !row.is_processed).length;

                if (selected_count === 0) {
                    frappe.msgprint({
                        title: __('No URLs Selected'),
                        indicator: 'orange',
                        message: __('Please select at least one URL to process by checking the "Select" checkbox in the Sitemap URLs table.')
                    });
                    return;
                }

                frappe.confirm(
                    __(`Are you sure you want to process ${selected_count} selected URL(s)? They will be added to the Knowledge Base.`),
                    function () {
                        frappe.show_alert({
                            message: __('📚 Processing selected URLs...'),
                            indicator: 'blue'
                        }, 10);

                        frappe.call({
                            method: 'chatforge.chatbot.doctype.chatbot_settings.chatbot_settings.process_selected_urls',
                            args: {
                                docname: frm.doc.name
                            },
                            callback: function (r) {
                                if (r.message && r.message.success) {
                                    frappe.show_alert({
                                        message: r.message.message,
                                        indicator: 'green'
                                    }, 10);
                                    frm.reload_doc();
                                } else if (r.message && r.message.error) {
                                    frappe.msgprint({
                                        title: __('Error'),
                                        indicator: 'red',
                                        message: r.message.error
                                    });
                                }
                            }
                        });
                    }
                );
            }, __('Sitemap'));

            // ========== SELECT ALL / DESELECT ALL ==========
            frm.add_custom_button(__('Select All Unprocessed'), function () {
                (frm.doc.sitemap_urls || []).forEach(row => {
                    if (!row.is_processed) {
                        row.is_selected = 1;
                    }
                });
                frm.refresh_field('sitemap_urls');
                frm.dirty();
            }, __('Sitemap'));

            frm.add_custom_button(__('Deselect All'), function () {
                (frm.doc.sitemap_urls || []).forEach(row => {
                    row.is_selected = 0;
                });
                frm.refresh_field('sitemap_urls');
                frm.dirty();
            }, __('Sitemap'));

            // ========== COPY EMBED CODE BUTTON ==========
            frm.add_custom_button(__('Copy Embed Code'), function () {
                frm.call({
                    method: 'get_embed_code',
                    doc: frm.doc,
                    callback: function (r) {
                        if (r.message) {
                            frappe.utils.copy_to_clipboard(r.message);
                            frappe.show_alert({
                                message: __('Embed code copied to clipboard!'),
                                indicator: 'green'
                            }, 5);
                        }
                    }
                });
            }, __('Actions'));
        }

        // ========== EMBED CODE DISPLAY ==========
        if (!frm.is_new() && frm.doc.api_token) {
            frm.call({
                method: 'get_embed_code',
                doc: frm.doc,
                callback: function (r) {
                    if (r.message && !frm.embed_code_added) {
                        frm.embed_code_added = true;
                        $(frm.fields_dict.allowed_domains.wrapper).after(`
                            <div class="embed-code-section" style="margin-top: 20px;">
                                <h5 style="color: #8d99a6; font-weight: 500; margin-bottom: 10px;">
                                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" style="margin-right: 5px; vertical-align: text-bottom;" viewBox="0 0 16 16">
                                        <path d="M5.854 4.854a.5.5 0 1 0-.708-.708l-3.5 3.5a.5.5 0 0 0 0 .708l3.5 3.5a.5.5 0 0 0 .708-.708L2.707 8l3.147-3.146zm4.292 0a.5.5 0 0 1 .708-.708l3.5 3.5a.5.5 0 0 1 0 .708l-3.5 3.5a.5.5 0 0 1-.708-.708L13.293 8l-3.147-3.146z"/>
                                    </svg>
                                    Widget Embed Code
                                </h5>
                                <div style="position: relative;">
                                    <pre id="embed-code-display" style="background: #1e1e1e; color: #d4d4d4; padding: 15px; border-radius: 8px; font-size: 12px; overflow-x: auto; white-space: pre-wrap; word-break: break-all;">${frappe.utils.escape_html(r.message)}</pre>
                                    <button id="copy-embed-btn" class="btn btn-primary btn-sm" style="position: absolute; top: 10px; right: 10px;">
                                        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" fill="currentColor" viewBox="0 0 16 16" style="margin-right: 4px;">
                                            <path d="M4 2a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V2zm2-1a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V2a1 1 0 0 0-1-1H6zM2 5a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1v-1h1v1a2 2 0 0 1-2 2H2a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h1v1H2z"/>
                                        </svg>
                                        Copy
                                    </button>
                                </div>
                                <p style="color: #8d99a6; font-size: 12px; margin-top: 10px;">
                                    <strong>Instructions:</strong> Copy this code and paste it before the closing <code>&lt;/body&gt;</code> tag of your website.
                                </p>
                            </div>
                        `);

                        $('#copy-embed-btn').on('click', function () {
                            frappe.utils.copy_to_clipboard(r.message);
                            frappe.show_alert({
                                message: __('Embed code copied to clipboard!'),
                                indicator: 'green'
                            }, 3);
                        });
                    }
                }
            });
        }

        // ========== STATUS INDICATOR ==========
        if (frm.doc.enabled) {
            frm.page.set_indicator(__('Active'), 'green');
        } else {
            frm.page.set_indicator(__('Disabled'), 'red');
        }

        // ========== SHOW SITEMAP STATS ==========
        if (!frm.is_new() && frm.doc.sitemap_urls && frm.doc.sitemap_urls.length > 0) {
            let total = frm.doc.sitemap_urls.length;
            let processed = frm.doc.sitemap_urls.filter(r => r.is_processed).length;
            let selected = frm.doc.sitemap_urls.filter(r => r.is_selected && !r.is_processed).length;

            frm.dashboard.add_indicator(
                __(`Total URLs: ${total} | Processed: ${processed} | Selected: ${selected}`),
                processed === total ? 'green' : 'blue'
            );
        }
    }
});
