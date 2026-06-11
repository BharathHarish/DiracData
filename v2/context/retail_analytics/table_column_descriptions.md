# Schema Column Descriptions And Sample Values: retail_analytics

This document contains per-table column semantics and sample value evidence.

## addresses

This table stores the physical and mailing addresses associated with clients. Each record represents a unique address and includes full street details, city, county, state, ZIP code, country, timezone offset, and the type of dwelling (apartment, condo, or single family). It is used to support geographic segmentation and regional analysis of customers across purchases, refunds, and marketing activity. All addresses in this dataset are located in the United States. The table links to client records, billing addresses, and shipping addresses across the store, online, and mail-order purchase channels.

### addresses.address_code

Short: Business-facing code that uniquely identifies an address.

Long: An alphanumeric code that serves as the business-facing identifier for each address record. It uniquely identifies an address and can be used to reference or look up a specific location across systems. The format appears to be a fixed-length encoded string.

Sample values: No non-null sample values available.

### addresses.address_record

Short: Internal unique identifier for each address record.

Long: A system-generated numeric identifier that uniquely identifies each row in the addresses table. This is an internal record key used to join address data to clients, purchases, and refunds. It does not carry direct business meaning on its own.

Sample values: No non-null sample values available.

### addresses.city

Short: City for the client address.

Long: The city name associated with the client's address. Used for geographic segmentation and regional analysis of customers, purchases, and returns. The dataset includes 695 distinct cities across the United States. A small percentage of records have no city on file.

Sample values: No non-null sample values available.

### addresses.country

Short: Country for the client address. All records are United States.

Long: The country associated with the client's address. All populated records in this dataset contain 'United States', indicating the business serves a domestic US customer base. A small percentage of records have no country value on file.

Sample values: No non-null sample values available.

### addresses.county

Short: The county associated with the client's address.

Long: Stores the county name for the client's address, such as 'Washington County' or 'Jefferson County'. Useful for sub-state geographic analysis. The dataset includes over 1,800 distinct counties. A small percentage of records have no county on file.

Sample values: No non-null sample values available.

### addresses.location_type

Short: The type of dwelling at the address, such as apartment, condo, or single family home.

Long: Classifies the residential dwelling type associated with the address. Values are apartment, condo, or single family. The three types are roughly equally distributed across the dataset. This attribute can be used to segment customers by housing type for demographic or marketing analysis. A small percentage of records have no location type on file.

Sample values: No non-null sample values available.

### addresses.state

Short: US state abbreviation for the client address.

Long: The two-letter US state abbreviation for the client's address, such as TX, GA, or VA. Covers all 50 US states plus Washington DC. Used for state-level geographic segmentation and regional sales analysis. A small percentage of records have no state on file.

Sample values: No non-null sample values available.

### addresses.street_name

Short: The name of the street for the client's address.

Long: Contains the street name portion of the address, such as 'Main', 'Oak', or 'Park'. Common street names appear frequently across the dataset. A small percentage of records have no street name on file.

Sample values: No non-null sample values available.

### addresses.street_number

Short: The numeric portion of the street address, such as a house or building number.

Long: Stores the street number component of a client's address, representing the house, building, or unit number on a street. Values range from 1 to 999. A small percentage of records have no street number on file.

Sample values: No non-null sample values available.

### addresses.street_type

Short: The type or suffix of the street, such as Avenue, Boulevard, or Road.

Long: Describes the street type or suffix associated with the address, for example Avenue, Boulevard, Drive, Lane, Road, Street, or Way. Both abbreviated and full forms are present (e.g., 'Ave' and 'Avenue'). A small percentage of records have no street type on file.

Sample values: No non-null sample values available.

### addresses.suite_number

Short: The suite or unit designation within a building, if applicable.

Long: Stores the suite or unit identifier for addresses that include a secondary location within a building, such as 'Suite A' or 'Suite 100'. Not all addresses have a suite number; a small percentage of records have this field blank.

Sample values: No non-null sample values available.

### addresses.timezone_offset

Short: UTC timezone offset for the address location, ranging from -10 to -5 hours.

Long: The UTC offset in hours for the timezone associated with the address. Values range from -10.00 (Hawaii) to -5.00 (Eastern US), reflecting US time zones. The most common offsets are -6.00 (Central) and -5.00 (Eastern). Useful for analyzing purchase timing relative to local time. A small percentage of records have no timezone offset on file.

Sample values: No non-null sample values available.

### addresses.zip

Short: Postal ZIP code for the client address.

Long: The five-digit US postal ZIP code for the client's address. Supports fine-grained geographic analysis at the postal area level. The dataset includes nearly 3,700 distinct ZIP codes. A small percentage of records have no ZIP code on file.

Sample values: No non-null sample values available.

## calendar_days

This table provides a comprehensive date dimension covering every calendar day from 1900 through 2100. Each row represents a single date and includes attributes for standard calendar reporting (year, quarter, month, week, day of week, day of month) as well as fiscal calendar equivalents (fiscal year, fiscal quarter sequence, fiscal week sequence). It also includes flags and indicators for holidays, weekends, days following a holiday, first and last days of the month, and whether a date falls within the current day, week, month, quarter, or year. Cross-period comparison fields such as same day last year and same day last quarter support year-over-year and quarter-over-quarter trend analysis. This table is referenced by purchase, refund, stock level, and campaign tables to enable time-based reporting.

### calendar_days.calendar_day

Short: The actual calendar date for this record.

Long: Stores the specific calendar date represented by each row, in date format (e.g., 2024-03-15). This is the primary human-readable date value used for filtering, grouping, and reporting by specific dates. The table spans dates from 1900 through 2100.

Sample values: No non-null sample values available.

### calendar_days.calendar_day_code

Short: Business-facing code that uniquely identifies a calendar day.

Long: An alphanumeric code serving as the business-facing identifier for each calendar day record. It uniquely identifies a date and can be used to reference a specific day across systems. The format appears to be a fixed-length encoded string.

Sample values: No non-null sample values available.

### calendar_days.calendar_day_record

Short: Internal unique identifier for each calendar day record.

Long: A system-generated numeric identifier that uniquely identifies each row in the calendar_days table. This is an internal record key used to join date dimension data to fact tables such as purchases, refunds, and stock levels. It does not represent a human-readable date.

Sample values: No non-null sample values available.

### calendar_days.current_day

Short: Flags whether this date is today (Y/N). All values are currently N in this dataset.

Long: A flag indicating whether the calendar date is the current day at the time the data was last refreshed. All values in this dataset are 'N', suggesting the data snapshot does not mark any date as today, or the flag has not been updated.

Sample values: No non-null sample values available.

### calendar_days.current_month

Short: Flags whether this date falls in the current calendar month (Y/N).

Long: A flag indicating whether the calendar date falls within the current calendar month at the time the data was last refreshed. A small number of dates (31) are marked 'Y', consistent with one active month. Used to filter month-to-date reporting.

Sample values: No non-null sample values available.

### calendar_days.current_quarter

Short: Flags whether this date falls in the current calendar quarter (Y/N).

Long: A flag indicating whether the calendar date falls within the current calendar quarter at the time the data was last refreshed. Approximately 91 dates are marked 'Y', consistent with one active quarter. Used to filter quarter-to-date reporting.

Sample values: No non-null sample values available.

### calendar_days.current_week

Short: Flags whether this date falls in the current week (Y/N). All values are currently N in this dataset.

Long: A flag indicating whether the calendar date falls within the current week at the time the data was last refreshed. All values in this dataset are 'N', suggesting the flag has not been updated or no date is marked as within the current week.

Sample values: No non-null sample values available.

### calendar_days.current_year

Short: Flags whether this date falls in the current calendar year (Y/N).

Long: A flag indicating whether the calendar date falls within the current calendar year at the time the data was last refreshed. Approximately 365 dates are marked 'Y', consistent with one active year. Used to filter year-to-date reporting.

Sample values: No non-null sample values available.

### calendar_days.day_name

Short: The full name of the day of the week, such as Monday or Friday.

Long: The human-readable name of the day of the week (Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday). Used for day-of-week analysis and reporting in business-friendly labels rather than numeric codes.

Sample values: No non-null sample values available.

### calendar_days.day_of_month

Short: The day number within the month, from 1 to 31.

Long: The numeric day within the calendar month, ranging from 1 to 31. Used for day-of-month analysis, such as identifying purchase patterns around paydays or end-of-month periods.

Sample values: No non-null sample values available.

### calendar_days.day_of_week

Short: Numeric day of the week, where 0 through 6 represent the days Sunday through Saturday (or similar convention).

Long: A numeric representation of the day of the week, with values from 0 to 6. The exact mapping of numbers to day names is confirmed by the companion day_name column (e.g., 4 = Thursday based on top values). Used for weekday vs. weekend analysis and day-of-week sales patterns.

Sample values: No non-null sample values available.

### calendar_days.first_day_of_month

Short: Reference to the first day of the month containing this date.

Long: Stores a reference (as a calendar day record key) pointing to the first day of the calendar month that contains this date. Useful for month-to-date calculations and grouping all days in a month back to a common anchor date.

Sample values: No non-null sample values available.

### calendar_days.fiscal_quarter_sequence

Short: A sequential number identifying the fiscal quarter across all fiscal years.

Long: A monotonically increasing integer that uniquely identifies each fiscal quarter across the full fiscal calendar range. Useful for ordering fiscal quarters chronologically or calculating the number of fiscal quarters between two dates.

Sample values: No non-null sample values available.

### calendar_days.fiscal_week_sequence

Short: A sequential number identifying the fiscal week across all fiscal years.

Long: A monotonically increasing integer that uniquely identifies each fiscal week across the full fiscal calendar range. Useful for ordering fiscal weeks chronologically or performing fiscal week-over-week comparisons.

Sample values: No non-null sample values available.

### calendar_days.fiscal_year

Short: The fiscal year associated with the calendar date.

Long: The four-digit fiscal year assigned to the date, which may differ from the calendar year depending on the business's fiscal calendar. Values range from 1900 to 2100. Used for financial reporting aligned to the company's fiscal periods.

Sample values: No non-null sample values available.

### calendar_days.following_holiday

Short: Indicates whether the date immediately follows a public holiday (Y/N).

Long: A flag indicating whether the calendar date falls on the day immediately after a public holiday. Values are 'Y' or 'N'. Useful for analyzing post-holiday shopping behavior and sales patterns.

Sample values: No non-null sample values available.

### calendar_days.holiday

Short: Indicates whether the date is a public holiday (Y/N).

Long: A flag indicating whether the calendar date is a recognized public holiday. Values are 'Y' (holiday) or 'N' (not a holiday). Approximately 650 days in the dataset are marked as holidays. Used to analyze the impact of holidays on sales, traffic, and returns.

Sample values: No non-null sample values available.

### calendar_days.last_day_of_month

Short: Reference to the last day of the month containing this date.

Long: Stores a reference (as a calendar day record key) pointing to the last day of the calendar month that contains this date. Useful for end-of-month reporting and calculating days remaining in a month.

Sample values: No non-null sample values available.

### calendar_days.month_of_year

Short: Month number within the year, from 1 (January) to 12 (December).

Long: The numeric month within the calendar year, where 1 represents January and 12 represents December. Used for monthly reporting, seasonal analysis, and filtering purchases or returns by month.

Sample values: No non-null sample values available.

### calendar_days.month_sequence

Short: A sequential number identifying the month across all years in the calendar.

Long: A monotonically increasing integer that uniquely identifies each calendar month across the full date range, starting from 0. Useful for ordering months chronologically or calculating the number of months between two dates without reference to year boundaries.

Sample values: No non-null sample values available.

### calendar_days.quarter_name

Short: A human-readable label for the calendar quarter, such as '2024Q3'.

Long: A formatted label combining the calendar year and quarter number, for example '2024Q3'. Used for labeling quarterly reports and charts in a business-friendly format.

Sample values: No non-null sample values available.

### calendar_days.quarter_of_year

Short: The quarter number within the calendar year, from 1 (Q1) to 4 (Q4).

Long: Identifies which quarter of the calendar year the date falls in: 1 for Q1 (January–March), 2 for Q2 (April–June), 3 for Q3 (July–September), and 4 for Q4 (October–December). Used for quarterly reporting and seasonal trend analysis.

Sample values: No non-null sample values available.

### calendar_days.quarter_sequence

Short: A sequential number identifying the quarter across all years in the calendar.

Long: A monotonically increasing integer that uniquely identifies each calendar quarter across the full date range. Useful for ordering quarters chronologically or calculating the number of quarters between two dates.

Sample values: No non-null sample values available.

### calendar_days.same_day_last_quarter

Short: Reference to the equivalent date one quarter prior, for quarter-over-quarter comparison.

Long: Stores a reference (as a calendar day record key) pointing to the same relative day in the prior calendar quarter. Enables quarter-over-quarter comparisons of sales, returns, and other metrics.

Sample values: No non-null sample values available.

### calendar_days.same_day_last_year

Short: Reference to the equivalent date one year prior, for year-over-year comparison.

Long: Stores a reference (as a calendar day record key) pointing to the same calendar day in the prior year. Enables year-over-year comparisons of sales, returns, and other metrics without complex date arithmetic.

Sample values: No non-null sample values available.

### calendar_days.week_sequence

Short: A sequential number identifying the week across all years in the calendar.

Long: A monotonically increasing integer that uniquely identifies each calendar week across the full date range. Useful for ordering weeks chronologically or calculating week-over-week differences across year boundaries.

Sample values: No non-null sample values available.

### calendar_days.weekend

Short: Indicates whether the date falls on a weekend (Y/N).

Long: A flag indicating whether the calendar date is a Saturday or Sunday. Values are 'Y' (weekend) or 'N' (weekday). Approximately 28% of dates are weekends. Used to compare weekday versus weekend purchasing behavior.

Sample values: No non-null sample values available.

### calendar_days.year

Short: Calendar year for the date record.

Long: The four-digit calendar year associated with the date, ranging from 1900 to 2100. Used for annual reporting, year-over-year comparisons, and filtering data to specific years.

Sample values: No non-null sample values available.

## client_profiles

This table contains demographic profile records used to classify individual shoppers into segments for analysis. Each record captures a combination of demographic attributes: gender (male or female), marital status (single, married, divorced, widowed, or unknown), education level (ranging from primary through advanced degree), an estimated annual purchase amount, credit rating (Good, Low Risk, High Risk, or Unknown), and counts of total dependents, employed dependents, and dependents in college. With nearly 1.9 million rows, this table represents a large set of demographic combinations. Client records link to their current active profile via a reference key. These segments are used to analyze purchasing behavior, revenue, and campaign response across different customer groups.

### client_profiles.client_profile_record

Short: Internal unique identifier for each client demographic profile record.

Long: A system-generated numeric identifier that uniquely identifies each row in the client_profiles table. This is an internal record key used to link demographic profile data to client records and purchase or refund transactions. It does not carry direct business meaning on its own.

Sample values: No non-null sample values available.

### client_profiles.credit_rating

Short: Client credit rating segment: Good, Low Risk, High Risk, or Unknown.

Long: Indicates the credit risk classification assigned to the client. Values are 'Good', 'Low Risk', 'High Risk', and 'Unknown', distributed equally across the dataset. Used for risk-based segmentation and analysis of purchasing behavior or payment patterns.

Sample values: No non-null sample values available.

### client_profiles.dependent_college_count

Short: Number of dependents in the client's household currently attending college, from 0 to 6.

Long: The count of dependents in the client's household who are enrolled in college, ranging from 0 to 6. Used for household lifecycle segmentation, particularly to identify families with college-age dependents, which may influence spending patterns and product preferences.

Sample values: No non-null sample values available.

### client_profiles.dependent_count

Short: Total number of dependents in the client's household, from 0 to 6.

Long: The total count of dependents associated with the client's household profile, ranging from 0 to 6. Used for household size segmentation and analysis of how family size relates to purchasing behavior or product preferences.

Sample values: No non-null sample values available.

### client_profiles.dependent_employed_count

Short: Number of employed dependents in the client's household, from 0 to 6.

Long: The count of dependents in the client's household who are employed, ranging from 0 to 6. Used alongside total dependent count to understand household income dynamics and segment clients by household employment profile.

Sample values: No non-null sample values available.

### client_profiles.education_status

Short: Client education segment, ranging from Primary through Advanced Degree.

Long: Describes the highest level of education attained by the client. Values include Primary, Secondary, College, 2 yr Degree, 4 yr Degree, Advanced Degree, and Unknown. All seven categories are equally distributed in this dataset. Used for demographic segmentation and analysis of purchasing patterns by education level.

Sample values: No non-null sample values available.

### client_profiles.gender

Short: Client gender segment: Male (M) or Female (F).

Long: Indicates the gender of the client associated with this demographic profile. Values are 'M' (male) and 'F' (female), distributed equally across the dataset. Used for gender-based segmentation of purchasing behavior, campaign targeting, and revenue analysis.

Sample values: No non-null sample values available.

### client_profiles.marital_status

Short: Client marital status segment: Single, Married, Divorced, Widowed, or Unknown.

Long: Indicates the marital status of the client. Values are 'S' (single), 'M' (married), 'D' (divorced), 'W' (widowed), and 'U' (unknown), distributed equally across the dataset. Used for household and lifestyle segmentation in customer and sales analysis.

Sample values: No non-null sample values available.

### client_profiles.purchase_estimate

Short: Estimated annual purchase amount for the client, in dollars.

Long: A banded estimate of the client's expected annual spending, ranging from $500 to $10,000 in $500 increments. This is a demographic segment attribute rather than an actual measured spend figure. Used to classify clients by estimated buying power for targeting and segmentation purposes.

Sample values: No non-null sample values available.

## clients

The clients table is the central record for every shopper or customer account in the business. Each row represents one person and links to their current mailing address, demographic profile, and household profile. The table also captures key tenure milestones such as the date of the customer's first purchase and first shipment, along with personal details like name, salutation, date of birth, birth country, email address, and a preferred-customer flag. It serves as the starting point for customer segmentation, loyalty analysis, lifetime value reporting, and cross-channel purchase history.

### clients.birth_country

Short: Country where the customer was born, used for demographic and geographic analysis.

Long: The full name of the country where the customer was born. The data includes a wide range of countries from around the world. This field supports demographic diversity analysis and international customer segmentation. Approximately 3.4% of clients have no birth country recorded (null).

Sample values: No non-null sample values available.

### clients.birth_day

Short: Day of the month the customer was born, used for birthday-based analysis.

Long: The numeric day of the month (1–31) representing the customer's birthday. Combined with birth_month and birth_year, this forms the customer's full date of birth. Used for age calculation, birthday promotions, and demographic analysis. Approximately 3.5% of clients have no birth day recorded (null).

Sample values: No non-null sample values available.

### clients.birth_month

Short: Month the customer was born, used for age and birthday analysis.

Long: The numeric month (1–12) representing the customer's birth month. Combined with birth_day and birth_year, this forms the customer's full date of birth. Used for age segmentation, birthday campaigns, and demographic reporting. Approximately 3.4% of clients have no birth month recorded (null).

Sample values: No non-null sample values available.

### clients.birth_year

Short: Year the customer was born, used to calculate age and generational segments.

Long: The four-digit year of the customer's birth, ranging from 1924 to 1992 in the current data. Combined with birth_month and birth_day, this forms the full date of birth. Used to calculate customer age, define generational cohorts (e.g., Baby Boomers, Gen X, Millennials), and support age-based segmentation. Approximately 3.5% of clients have no birth year recorded (null).

Sample values: No non-null sample values available.

### clients.client_code

Short: Business-facing customer identifier used to reference a shopper across systems.

Long: The business-facing alphanumeric code that uniquely identifies each customer or shopper account. This is the preferred identifier for referencing a client in reports and cross-system lookups, as opposed to the internal client_record key. Every client has a client_code with no missing values.

Sample values: No non-null sample values available.

### clients.client_record

Short: Internal system identifier uniquely identifying each client record.

Long: A unique numeric identifier assigned to each client row in the system. This is an internal record key used to join the clients table to other tables. Business users should use client_code as the customer-facing identifier. Every client has exactly one client_record value with no nulls.

Sample values: No non-null sample values available.

### clients.current_address_ref

Short: Links the client to their current mailing or residential address.

Long: A reference to the client's active address record in the addresses table. The address provides city, state, ZIP, county, and country details used for geographic analysis of the customer base. All clients have an address linked with no missing values.

Sample values: No non-null sample values available.

### clients.current_client_profile_ref

Short: Links the client to their active demographic profile record.

Long: A reference to the client's currently active demographic profile in the client_profiles table. The profile captures segments such as gender, marital status, education level, credit rating, and purchase estimate. Approximately 3.4% of clients have no profile linked (null). This field enables demographic segmentation of customers for marketing and analytics.

Sample values: No non-null sample values available.

### clients.current_household_profile_ref

Short: Links the client to their active household profile record.

Long: A reference to the client's currently active household profile in the household_profiles table. The household profile captures economic and lifestyle segments such as income range, buying potential, number of dependents, and vehicle count. Approximately 3.4% of clients have no household profile linked (null). This field supports household-level segmentation and income-based analysis.

Sample values: No non-null sample values available.

### clients.email_address

Short: Customer's email address used for digital communications and account identification.

Long: The email address on file for the customer, used for digital marketing, order confirmations, and account communications. Nearly all clients have a unique email address. Approximately 3.5% of clients have no email address recorded (null). This field can be used to identify customers for email campaign targeting and response analysis.

Sample values: No non-null sample values available.

### clients.first_name

Short: Customer's first or given name.

Long: The first name of the customer or shopper. Used for personalized communications, customer lookup, and name-based reporting. Approximately 3.5% of clients have no first name recorded (null).

Sample values: No non-null sample values available.

### clients.first_sale_calendar_day_ref

Short: Calendar day of the customer's first purchase, indicating when they first bought from the business.

Long: A reference to the calendar_days table identifying the date of the customer's very first purchase or sale. This is a key customer tenure metric used for cohort analysis, loyalty tracking, and understanding customer acquisition timing. Approximately 3.5% of clients have no first sale date recorded (null), likely indicating customers who have not yet made a purchase.

Sample values: No non-null sample values available.

### clients.first_shipping_calendar_day_ref

Short: Calendar day of the customer's first shipment, indicating when they first received an order.

Long: A reference to the calendar_days table identifying the date on which the customer received their very first shipment. This is a customer tenure indicator useful for cohort analysis and understanding how long a customer has been receiving orders. Approximately 3.4% of clients have no first shipping date recorded (null), likely indicating customers who have not yet received a shipment.

Sample values: No non-null sample values available.

### clients.last_name

Short: Customer's last name or surname.

Long: The surname or family name of the customer. Used alongside first_name for customer identification, lookup, and personalized outreach. Approximately 3.5% of clients have no last name recorded (null).

Sample values: No non-null sample values available.

### clients.last_review_calendar_day_ref

Short: Calendar day of the most recent review or account assessment for the customer.

Long: A reference to the calendar_days table indicating the most recent date on which the customer's account was reviewed or assessed. The values in the current data span approximately one year. The exact business process driving this review is not fully described in the available context, but it likely relates to account maintenance, credit review, or customer relationship management. Approximately 3.5% of clients have no review date recorded (null).

Sample values: No non-null sample values available.

### clients.login

Short: Customer's login or username for online account access.

Long: The login or username associated with the customer's online account. In the current data, this field appears to be largely unpopulated — nearly all non-null values are empty strings, with approximately 3.5% null. The business meaning is uncertain given the lack of populated values; it may represent a legacy or unused field.

Sample values: No non-null sample values available.

### clients.preferred_client_flag

Short: Indicates whether the customer is a preferred or loyalty-tier shopper.

Long: A flag indicating whether the customer holds preferred or loyalty status with the business. Values are 'Y' (preferred) or 'N' (not preferred). Roughly half of all clients are flagged as preferred customers. Approximately 3.4% have no value recorded (null). This field is useful for loyalty program analysis and segmenting high-value shoppers.

Sample values: No non-null sample values available.

### clients.salutation

Short: Customer's preferred title or salutation, such as Mr., Mrs., Dr., or Ms.

Long: The honorific or title associated with the customer's name. Observed values include Dr., Mr., Mrs., Ms., Miss, and Sir. This field is used for personalized communications and customer correspondence. Approximately 3.4% of clients have no salutation recorded.

Sample values: No non-null sample values available.

## clock_times

The clock_times table is a time-of-day dimension containing one row for every second in a 24-hour day (86,400 rows total). It supports analysis of when purchases, returns, or other events occur throughout the day. Each record provides the exact hour, minute, and second, along with AM/PM indicator, work shift (first, second, third), sub-shift period (morning, afternoon, evening, night), and meal period (breakfast, lunch, dinner). This table is used to answer questions such as which shift drives the most sales or whether purchases spike during lunch hours.

### clock_times.am_pm

Short: Indicates whether the time falls in the AM or PM half of the day.

Long: A two-value indicator showing whether a given time is in the AM (midnight to noon) or PM (noon to midnight) period. Exactly half of all records are AM and half are PM. Used for broad time-of-day segmentation in sales and activity reporting.

Sample values: No non-null sample values available.

### clock_times.clock_time

Short: Numeric representation of the time of day in seconds since midnight.

Long: A numeric value representing the time of day as the number of seconds elapsed since midnight (0 = midnight, 86,399 = 11:59:59 PM). This field provides a sortable, continuous measure of time used for ordering and filtering events by time of day. Each value is unique across the 86,400 rows.

Sample values: No non-null sample values available.

### clock_times.clock_time_code

Short: Business-facing alphanumeric code identifying each second-level time record.

Long: An alphanumeric business code uniquely identifying each second-level time record. This is the business-facing counterpart to the internal clock_time_record key and is used for referencing specific times in cross-system lookups. Every record has a unique code with no missing values.

Sample values: No non-null sample values available.

### clock_times.clock_time_record

Short: Internal system identifier uniquely identifying each second-level time record.

Long: A unique numeric identifier for each row in the clock_times table, representing one specific second of the day. Values range from 0 to 86,399, corresponding to the 86,400 seconds in a 24-hour day. This is an internal record key used to join time-of-day information to transaction and event tables.

Sample values: No non-null sample values available.

### clock_times.hour

Short: Hour of the day (0–23) for time-of-day analysis.

Long: The hour component of the time of day in 24-hour format, ranging from 0 (midnight) to 23 (11 PM). Used to analyze when purchases, returns, or other business events occur by hour. Each hour contains exactly 3,600 second-level records.

Sample values: No non-null sample values available.

### clock_times.meal_clock_time

Short: Meal period associated with the time of day, such as breakfast, lunch, or dinner.

Long: Classifies the time of day into a meal period: breakfast, lunch, dinner, or no meal period (empty string). The majority of the day (50,400 seconds) falls outside a defined meal period. Breakfast covers 14,400 seconds, while lunch and dinner each cover 10,800 seconds. Used to analyze whether purchase or activity timing correlates with meal periods.

Sample values: No non-null sample values available.

### clock_times.minute

Short: Minute within the hour (0–59) for time-of-day analysis.

Long: The minute component of the time of day, ranging from 0 to 59. Used in combination with hour and second to pinpoint the exact time of a business event. Each minute value appears 1,440 times across the table (once per hour per day).

Sample values: No non-null sample values available.

### clock_times.second

Short: Second within the minute (0–59) for precise time-of-day analysis.

Long: The second component of the time of day, ranging from 0 to 59. Provides the most granular level of time detail available in this dimension. Each second value appears 1,440 times across the table. Used when precise event timing is required.

Sample values: No non-null sample values available.

### clock_times.shift

Short: Work shift period (first, second, or third) associated with the time of day.

Long: Classifies the time of day into one of three work shifts: first, second, or third. Each shift covers eight hours of the day, with 28,800 records per shift. Used to analyze sales, staffing, and operational activity by shift period.

Sample values: No non-null sample values available.

### clock_times.sub_shift

Short: Finer time-of-day period such as morning, afternoon, evening, or night.

Long: A more granular time-of-day classification than shift, dividing the day into morning, afternoon, evening, and night periods. Evening is the largest segment (25,200 records), followed by morning and night (21,600 each), and afternoon (18,000). Used to analyze customer behavior and sales patterns at a sub-shift level.

Sample values: No non-null sample values available.

## delivery_methods

The delivery_methods table is a small reference table describing the 20 shipping and delivery options available to customers. Each record identifies the service type (such as EXPRESS, NEXT DAY, OVERNIGHT, REGULAR, or TWO DAY), the transport mode or code (AIR, BIKE, SEA, SURFACE), the carrier name (such as FedEx, UPS, DHL, or USPS), and the associated carrier contract identifier. This table is used to analyze order fulfillment by shipping speed, carrier performance, and delivery channel across store, online, and mail-order purchases.

### delivery_methods.carrier

Short: Name of the shipping carrier or logistics provider for the delivery method.

Long: The name of the carrier or logistics company responsible for delivering orders under this method. Carriers include well-known providers such as FedEx, UPS, DHL, and USPS, as well as other carriers such as Airborne, Alliance, and others. Each of the 20 delivery methods uses a distinct carrier. Used to analyze carrier performance, cost, and volume.

Sample values: No non-null sample values available.

### delivery_methods.code

Short: Transport mode code for the delivery method, such as Air, Sea, or Surface.

Long: A short code indicating the mode of transport used for the delivery method. Observed values are AIR, SEA, SURFACE, and BIKE. SEA and SURFACE are the most common modes. This field supports analysis of shipping channel mix and logistics costs by transport mode.

Sample values: No non-null sample values available.

### delivery_methods.contract

Short: Contract identifier associated with the carrier agreement for this delivery method.

Long: An alphanumeric identifier representing the specific carrier contract or agreement governing this delivery method. Each delivery method has a unique contract code. The exact business meaning of the contract values is not fully described in the available context, but this field likely links to carrier pricing or service-level agreements. Used for contract management and cost analysis.

Sample values: No non-null sample values available.

### delivery_methods.delivery_method_code

Short: Business-facing alphanumeric code identifying each delivery method.

Long: An alphanumeric business code uniquely identifying each delivery method. This is the business-facing counterpart to the internal delivery_method_record key and is used for cross-system referencing of shipping options. Every record has a unique code with no missing values.

Sample values: No non-null sample values available.

### delivery_methods.delivery_method_record

Short: Internal system identifier uniquely identifying each delivery method record.

Long: A unique numeric identifier for each row in the delivery_methods table. Values range from 1 to 20, corresponding to the 20 available shipping and delivery options. This is an internal record key used to join delivery method details to purchase and refund transaction tables.

Sample values: No non-null sample values available.

### delivery_methods.type

Short: Delivery service type or speed, such as Express, Next Day, Overnight, or Regular.

Long: Describes the service level or speed of the delivery method. Observed values include EXPRESS, NEXT DAY, OVERNIGHT, REGULAR, TWO DAY, and LIBRARY. This field is used to analyze order fulfillment by shipping speed and to understand customer preferences for delivery urgency. EXPRESS and NEXT DAY are the most common types in the current data.

Sample values: No non-null sample values available.

## fulfillment_centers

The fulfillment_centers table describes the five warehouse or distribution center locations used by the business to store inventory and ship orders. Each record includes the warehouse name, physical size in square feet, and a full mailing address (street, city, county, state, ZIP, country, and timezone offset). This table is referenced by purchase and stock-level records to identify which warehouse fulfilled an order or holds a given product. All five centers in the current data appear to be located in Fairview, TN, United States. Some address and size fields have a small number of null values.

### fulfillment_centers.city

Short: City where the fulfillment center is located.

Long: The city in which the fulfillment center or warehouse is located. All five centers in the current data are located in Fairview. Used for geographic analysis of warehouse distribution and logistics coverage.

Sample values: No non-null sample values available.

### fulfillment_centers.country

Short: Country where the fulfillment center is located.

Long: The country in which the fulfillment center is located. All five centers in the current data are in the United States. Used for international logistics and geographic reporting.

Sample values: No non-null sample values available.

### fulfillment_centers.county

Short: County where the fulfillment center is located.

Long: The county in which the fulfillment center is located. All five centers in the current data are in Williamson County. Used for geographic and jurisdictional analysis of warehouse locations.

Sample values: No non-null sample values available.

### fulfillment_centers.fulfillment_center_code

Short: Business-facing alphanumeric code identifying each fulfillment center or warehouse.

Long: An alphanumeric business code uniquely identifying each fulfillment center or warehouse location. This is the business-facing counterpart to the internal fulfillment_center_record key and is used for cross-system referencing of warehouse locations. Every record has a unique code with no missing values.

Sample values: No non-null sample values available.

### fulfillment_centers.fulfillment_center_record

Short: Internal system identifier uniquely identifying each fulfillment center record.

Long: A unique numeric identifier for each row in the fulfillment_centers table. Values range from 1 to 5, corresponding to the five warehouse locations. This is an internal record key used to join fulfillment center details to purchase, refund, and stock-level tables.

Sample values: No non-null sample values available.

### fulfillment_centers.state

Short: US state where the fulfillment center is located.

Long: The US state abbreviation for the fulfillment center's location. All five centers in the current data are in TN (Tennessee). Used for geographic analysis and state-level logistics reporting.

Sample values: No non-null sample values available.

### fulfillment_centers.street_name

Short: Street name of the fulfillment center's physical address.

Long: The name of the street on which the fulfillment center is located. One of the five centers has a null value for this field. Used as part of the full mailing address for the warehouse.

Sample values: No non-null sample values available.

### fulfillment_centers.street_number

Short: Street number of the fulfillment center's physical address.

Long: The numeric portion of the street address for the fulfillment center location. One of the five centers has a null value for this field. Used as part of the full mailing address for the warehouse.

Sample values: No non-null sample values available.

### fulfillment_centers.street_type

Short: Street type suffix for the fulfillment center's address, such as Avenue, Drive, or Parkway.

Long: The street type or suffix (e.g., Avenue, Drive, Parkway) associated with the fulfillment center's street address. One of the five centers has a null value for this field. Used as part of the full mailing address for the warehouse.

Sample values: No non-null sample values available.

### fulfillment_centers.suite_number

Short: Suite or unit number within the fulfillment center's building address.

Long: The suite or unit designation within the building at the fulfillment center's address. One of the five centers has a null value for this field. Used as part of the full mailing address for the warehouse.

Sample values: No non-null sample values available.

### fulfillment_centers.timezone_offset

Short: UTC timezone offset for the fulfillment center's location.

Long: The UTC offset representing the local timezone of the fulfillment center. All populated values in the current data are -5.00 (Eastern Standard Time / UTC-5). One of the five centers has a null value for this field. Used to align order and shipment timestamps across different time zones.

Sample values: No non-null sample values available.

### fulfillment_centers.warehouse_name

Short: Name of the fulfillment center or warehouse facility.

Long: The descriptive name assigned to the fulfillment center or warehouse. In the current data, four of the five centers have names recorded; one has a null value. The names appear to be descriptive phrases rather than formal facility names, which may indicate placeholder or synthetic data. Used to identify and label warehouse locations in reporting.

Sample values: No non-null sample values available.

### fulfillment_centers.warehouse_square_ft

Short: Physical size of the warehouse in square feet.

Long: The total floor area of the fulfillment center measured in square feet. Values in the current data range from approximately 138,504 to 977,787 square feet, indicating significant variation in warehouse size. One of the five centers has a null value for this field. Used to understand warehouse capacity and scale for inventory and logistics planning.

Sample values: No non-null sample values available.

### fulfillment_centers.zip

Short: Postal ZIP code of the fulfillment center's location.

Long: The postal ZIP code for the fulfillment center's address. All five centers in the current data share the same ZIP code (35709). Used for geographic analysis and shipping zone calculations.

Sample values: No non-null sample values available.

## household_profiles

The household_profiles table contains segment records that classify households along key economic and lifestyle dimensions. Each record describes a unique combination of income range, estimated buying potential, number of dependents, and vehicle count. These segments are linked to clients through the clients table and are used to analyze purchasing behavior, target marketing campaigns, and understand the economic profile of shoppers. Buying potential is expressed as a dollar range (e.g., '1001-5000', '>10000'), making it useful for segmenting high-value versus low-value households. The table contains 7,200 distinct segment combinations and serves as a reference dimension for retail analytics across all purchase and refund channels.

### household_profiles.buy_potential

Short: Estimated annual spending potential of the household, expressed as a dollar range such as '0-500', '1001-5000', or '>10000'.

Long: The buy_potential field categorizes a household's estimated purchasing capacity into one of six spending bands: '0-500', '501-1000', '1001-5000', '5001-10000', '>10000', and 'Unknown'. This segment is useful for identifying high-value households, targeting promotions to shoppers with greater spending capacity, and analyzing how buying potential correlates with actual purchase amounts or order frequency. Each band is equally represented across the household profile records.

Sample values: No non-null sample values available.

### household_profiles.dependent_count

Short: Number of dependents in the household, ranging from 0 to 9.

Long: This field records how many dependents are associated with the household, with values ranging from 0 (no dependents) to 9. It is a household-level demographic attribute that can be used to segment customers by family size, study how household composition affects purchasing patterns, or target family-oriented promotions. All values from 0 to 9 are equally distributed across the 7,200 household profile records.

Sample values: No non-null sample values available.

### household_profiles.household_profile_record

Short: Internal unique identifier for each household profile segment record.

Long: This is the primary key for the household_profiles table. It uniquely identifies each household segment combination and is used as a reference key (household_profile_ref) in purchase and refund fact tables to link transactions to a household demographic profile. It has no standalone business meaning beyond serving as the internal record identifier.

Sample values: No non-null sample values available.

### household_profiles.income_range_ref

Short: Links the household profile to an income band in the income_ranges table, indicating the household's estimated annual income tier.

Long: This field references the income_ranges table and assigns each household profile to one of 20 income brackets. It enables analysts to group households by economic tier and study how income level influences purchasing behavior, buying potential, or campaign response. All 20 income bands are represented evenly across the 7,200 household profile records.

Sample values: No non-null sample values available.

### household_profiles.vehicle_count

Short: Number of vehicles owned by the household, with -1 indicating unknown or not applicable.

Long: This field captures the number of vehicles associated with the household, with values ranging from 0 to 4. A value of -1 appears to indicate that vehicle ownership is unknown or not recorded for that household segment. Vehicle count can serve as a proxy for household mobility or economic status and may be used in customer segmentation or lifestyle analysis. All six values (-1 through 4) are equally distributed across the household profile records.

Sample values: No non-null sample values available.

## income_ranges

The income_ranges table is a small reference dimension containing 20 income band records. Each record defines a contiguous income bracket with a lower bound and an upper bound expressed in dollars (e.g., $0–$10,000 up to $190,001–$200,000). These bands are used to classify households into economic tiers via the household_profiles table. Analysts and business users can use this table to filter or group customers by income level, compare purchasing behavior across income segments, or evaluate campaign effectiveness by economic tier. The bands are evenly spaced at $10,000 intervals, covering a range from $0 to $200,000.

### income_ranges.income_range_record

Short: Internal unique identifier for each income band record in the income_ranges reference table.

Long: This is the primary key for the income_ranges table. It uniquely identifies each of the 20 income bracket records and is referenced by the income_range_ref field in the household_profiles table. It has no standalone business meaning beyond serving as the internal record identifier for the income band.

Sample values: No non-null sample values available.

### income_ranges.lower_bound

Short: The minimum annual household income (in dollars) for this income band, e.g., $0, $10,001, $20,001.

Long: This field defines the lower boundary of an income bracket in dollars. Values range from $0 (the lowest band) to $190,001 (the highest band). Together with upper_bound, it defines a contiguous income range used to classify households into economic tiers. Analysts can use this field to filter or label income segments when studying how household income relates to purchasing behavior, campaign response, or customer lifetime value.

Sample values: No non-null sample values available.

### income_ranges.upper_bound

Short: The maximum annual household income (in dollars) for this income band, e.g., $10,000, $20,000, up to $200,000.

Long: This field defines the upper boundary of an income bracket in dollars. Values range from $10,000 (the lowest band ceiling) to $200,000 (the highest band ceiling). Together with lower_bound, it forms a complete income range definition. The 20 bands are evenly spaced at $10,000 intervals. This field is used alongside lower_bound to label income segments in reports and dashboards that analyze customer spending by economic tier.

Sample values: No non-null sample values available.

## mail_order_purchases

The mail_order_purchases table records every item sold through the mail-order (catalog or mailer) channel. With over 1.4 million rows, it is the primary fact table for mail-order commerce analysis. Each row represents a line item within an order and captures the full financial picture: wholesale cost, list price, actual sales price, extended amounts for quantity, discounts, delivery costs, tax, and multiple net-paid totals. Profitability is available via net_profit. The table links to calendar days for sale date and shipping date, clock times for time-of-day analysis, clients (both billing and shipping), demographic profiles (client and household), addresses (billing and shipping), the support center that handled the order, the mailer page that prompted the purchase, the delivery method used, the fulfillment center that shipped the order, the merchandise item sold, and the marketing campaign associated with the sale. This makes it suitable for analyzing mail-order revenue, order volume, discount effectiveness, coupon usage, delivery costs, campaign ROI, customer segments, and fulfillment performance.

### mail_order_purchases.billing_address_ref

Short: The billing address associated with the mail-order purchase, linking to the addresses table.

Long: This field references the addresses table and identifies the mailing or residential address used for billing on the mail-order order. It enables geographic analysis of mail-order revenue by city, state, ZIP code, or country based on the billing location. A small proportion of rows (~0.5%) have a null value.

Sample values: No non-null sample values available.

### mail_order_purchases.billing_client_profile_ref

Short: The demographic profile of the billing customer at the time of the mail-order purchase, linking to the client_profiles table.

Long: This field references the client_profiles table and captures the demographic segment (gender, marital status, education, credit rating, etc.) associated with the billing client at the time of the order. It enables segmentation of mail-order revenue and order volume by customer demographics. Because client profiles can change over time, this field preserves the profile that was active at the point of purchase.

Sample values: No non-null sample values available.

### mail_order_purchases.billing_client_ref

Short: The customer account responsible for payment on the mail-order purchase, linking to the clients table.

Long: This field references the clients table and identifies the shopper who was billed for the mail-order order. In cases where the billing and shipping clients differ (e.g., a gift order), this field captures the paying customer while shipping_client_ref captures the recipient. It is the primary client link for revenue and customer-level purchase analysis in the mail-order channel.

Sample values: No non-null sample values available.

### mail_order_purchases.billing_household_profile_ref

Short: The household segment of the billing customer at the time of the mail-order purchase, linking to the household_profiles table.

Long: This field references the household_profiles table and associates the billing client's household segment (income range, buying potential, dependents, vehicles) with the mail-order transaction. It supports analysis of mail-order purchasing behavior by household economic profile and lifestyle segment. A small proportion of rows (~0.5%) have a null value.

Sample values: No non-null sample values available.

### mail_order_purchases.campaign_ref

Short: The marketing campaign or promotional offer associated with the mail-order purchase, linking to the marketing_campaigns table.

Long: This field references the marketing_campaigns table and links the mail-order transaction to the campaign or promotional offer that influenced the sale. All 300 campaigns are represented in the mail-order purchase data. It is used to measure campaign ROI, compare order volumes across promotions, and analyze the impact of discounts or offers on mail-order revenue. A small proportion of rows (~0.5%) have a null value, indicating orders not attributed to a specific campaign.

Sample values: No non-null sample values available.

### mail_order_purchases.coupon_amount

Short: The total coupon or voucher savings applied to this mail-order line item.

Long: This field records the dollar value of any coupon or voucher applied to the order line. The vast majority of rows (~80%) have a coupon amount of $0.00, indicating that coupons are used on a minority of mail-order transactions. When applied, coupon values range up to over $26,000 for large-quantity orders. It is used to measure coupon redemption rates and the financial impact of coupon promotions. A small proportion of rows (~0.5%) have a null value.

Sample values: No non-null sample values available.

### mail_order_purchases.delivery_method_ref

Short: The shipping or delivery method used for the mail-order order, linking to the delivery_methods table.

Long: This field references the delivery_methods table and identifies the carrier, service type, and contract used to ship the mail-order order. All 20 delivery methods are used with roughly equal frequency across mail-order transactions. It supports analysis of delivery cost, carrier preference, and fulfillment efficiency. A small proportion of rows (~0.5%) have a null value.

Sample values: No non-null sample values available.

### mail_order_purchases.extended_delivery_cost

Short: The total shipping or delivery cost charged for this mail-order line item.

Long: This field records the total delivery or shipping cost associated with the order line, ranging from $0.00 (free shipping) to over $14,000 for large-quantity orders. A small number of rows have $0.00 delivery cost, suggesting some orders qualify for free shipping. It is used to analyze shipping cost by delivery method, fulfillment center, or order size, and is included in net-paid-with-delivery calculations. A small proportion of rows (~0.5%) have a null value.

Sample values: No non-null sample values available.

### mail_order_purchases.extended_discount_amount

Short: The total discount applied across all units in this mail-order line item (quantity × per-unit discount).

Long: This field captures the total dollar value of discounts applied to the order line, calculated as the per-unit discount multiplied by quantity. Values range from $0.00 (no discount) to over $28,000 for high-quantity orders. It is used to measure promotional generosity, analyze discount impact on revenue, and evaluate campaign effectiveness. A small proportion of rows (~0.5%) have a null value.

Sample values: No non-null sample values available.

### mail_order_purchases.extended_list_price

Short: The total value of this mail-order line item at the full list price before discounts (quantity × list price).

Long: This field records the total undiscounted value of the order line, calculated as quantity multiplied by the per-unit list price. It ranges from $1.27 to nearly $30,000. Comparing extended_list_price to extended_sales_price reveals the total discount value applied to the line. It is useful for measuring promotional depth and potential revenue foregone. A small proportion of rows (~0.5%) have a null value.

Sample values: No non-null sample values available.

### mail_order_purchases.extended_sales_price

Short: The total revenue from this mail-order line item at the actual sales price (quantity × sales price).

Long: This field records the total sales revenue for the order line, calculated as quantity multiplied by the per-unit sales price after discounts. It ranges from $0.00 to over $28,000. It is the primary revenue measure at the line-item level for mail-order sales analysis. A small proportion of rows (~0.5%) have a null value.

Sample values: No non-null sample values available.

### mail_order_purchases.extended_tax

Short: The total tax amount applied to this mail-order line item.

Long: This field records the total tax charged on the order line. A large proportion of rows have a tax value of $0.00, suggesting that many mail-order transactions are tax-exempt or shipped to non-taxable jurisdictions. Non-zero values range up to over $2,400 for high-value orders. It is used in net-paid-with-tax calculations. A small proportion of rows (~0.5%) have a null value.

Sample values: No non-null sample values available.

### mail_order_purchases.extended_wholesale_cost

Short: The total wholesale cost for this mail-order line item (quantity × wholesale cost per unit).

Long: This field records the total acquisition cost for the order line, calculated as quantity multiplied by the per-unit wholesale cost. It ranges from just over $1.00 to $9,999.00. It is used alongside extended_sales_price to calculate gross margin and profitability at the line-item level. A small proportion of rows (~0.5%) have a null value.

Sample values: No non-null sample values available.

### mail_order_purchases.fulfillment_center_ref

Short: The warehouse or fulfillment center that shipped the mail-order order, linking to the fulfillment_centers table.

Long: This field references the fulfillment_centers table and identifies which of the five fulfillment centers dispatched the mail-order shipment. All five centers handle roughly equal volumes of mail-order orders. It supports analysis of fulfillment center workload, shipping costs by location, and inventory utilization. A small proportion of rows (~0.5%) have a null value.

Sample values: No non-null sample values available.

### mail_order_purchases.list_price

Short: The standard retail or catalog list price per unit for the product in this mail-order line item.

Long: This field records the published or catalog list price per unit of the merchandise item, ranging from $1.01 to $300.00. It represents the full undiscounted price before any promotions or coupons are applied. Comparing list_price to sales_price reveals the discount depth applied to the order line. A small proportion of rows (~0.5%) have a null value.

Sample values: No non-null sample values available.

### mail_order_purchases.mailer_page_ref

Short: The catalog or mailer page that prompted the mail-order purchase, linking to the mailer_pages table.

Long: This field references the mailer_pages table and identifies the specific catalog or mailer page associated with the order. It is a key dimension for analyzing the effectiveness of print marketing materials in driving mail-order sales. By linking purchases back to mailer pages, analysts can evaluate which catalog sections, departments, or mailer editions generate the most revenue or order volume. A small proportion of rows (~0.5%) have a null value.

Sample values: No non-null sample values available.

### mail_order_purchases.merchandise_ref

Short: The product sold in the mail-order transaction, linking to the merchandise catalog table.

Long: This field references the merchandise table and identifies the specific product item included in the mail-order order line. It is a required field with no null values, confirming that every mail-order purchase row is associated with a product. It enables analysis of mail-order sales by product, brand, category, class, or manufacturer, and supports product-level revenue and profitability reporting.

Sample values: No non-null sample values available.

### mail_order_purchases.net_paid

Short: The net amount paid by the customer for this mail-order line item, excluding tax and delivery charges.

Long: This field records the total amount paid by the customer for the order line after discounts and coupons, but before tax and delivery costs are added. It ranges from $0.00 to over $28,000. It is the core revenue measure for mail-order sales analysis and is the basis for the tax and delivery variants (net_paid_with_tax, net_paid_with_delivery). A small proportion of rows (~0.5%) have a null value.

Sample values: No non-null sample values available.

### mail_order_purchases.net_paid_with_delivery

Short: The net amount paid by the customer for this mail-order line item, including delivery charges but excluding tax.

Long: This field adds the extended delivery cost to net_paid, giving the total customer payment inclusive of shipping but before tax. It ranges from $0.00 to over $40,000. It is used to analyze the total cost to the customer when delivery fees are included, and to evaluate the impact of delivery pricing on overall order value. This field has no null values.

Sample values: No non-null sample values available.

### mail_order_purchases.net_paid_with_delivery_tax

Short: The fully loaded amount paid by the customer for this mail-order line item, including both delivery charges and tax.

Long: This field represents the total amount paid by the customer for the order line, inclusive of all charges: the sales price after discounts and coupons, plus delivery costs, plus tax. It ranges from $0.00 to over $41,000 and is the most complete measure of customer spend at the line-item level for mail-order transactions. It is used in total revenue and customer spend analyses. This field has no null values.

Sample values: No non-null sample values available.

### mail_order_purchases.net_paid_with_tax

Short: The net amount paid by the customer for this mail-order line item, including tax but excluding delivery charges.

Long: This field adds the extended tax amount to net_paid, giving the total customer payment inclusive of tax but before delivery costs. It ranges from $0.00 to over $30,000. It is used in revenue reporting where tax-inclusive totals are required. A small proportion of rows (~0.5%) have a null value.

Sample values: No non-null sample values available.

### mail_order_purchases.net_profit

Short: The profit or loss on this mail-order line item, calculated as net revenue minus wholesale cost.

Long: This field records the net profit (or loss) generated by the order line, calculated as the difference between what the customer paid and the wholesale cost of the merchandise. Values range from approximately -$9,897 (a loss) to nearly $19,000 (a gain), with negative values indicating that the item was sold below cost, often due to heavy discounting or coupon usage. It is the primary profitability metric for mail-order line-item analysis and is used to evaluate margin performance by product, campaign, customer segment, or fulfillment center. This field has no null values.

Sample values: No non-null sample values available.

### mail_order_purchases.order_number

Short: The mail-order order identifier, grouping multiple line items that belong to the same customer order.

Long: This field contains the order number that groups individual product line items into a single customer order. With 160,000 distinct order numbers across 1.4 million rows, each order typically contains multiple line items. It is used to aggregate order-level totals, count distinct orders, and analyze basket size or multi-item purchasing behavior in the mail-order channel. This field has no null values.

Sample values: No non-null sample values available.

### mail_order_purchases.quantity

Short: The number of units of the product ordered in this mail-order line item.

Long: This field records how many units of the merchandise item were included in the order line, with values ranging from 1 to 100. It is used in conjunction with per-unit price fields to calculate extended amounts. Quantity is a key driver of extended sales price, extended wholesale cost, and extended delivery cost. A small proportion of rows (~0.5%) have a null value.

Sample values: No non-null sample values available.

### mail_order_purchases.sale_calendar_day_ref

Short: The calendar date when the mail-order purchase was placed, linking to the calendar_days reference table.

Long: This field references the calendar_days table and identifies the specific date on which the mail-order sale occurred. It enables time-based analysis such as daily, weekly, monthly, quarterly, and yearly sales trends for the mail-order channel. A small proportion of rows (~0.5%) have a null value for this field, which may indicate orders where the sale date was not recorded.

Sample values: No non-null sample values available.

### mail_order_purchases.sale_clock_time_ref

Short: The time of day when the mail-order purchase was placed, linking to the clock_times reference table.

Long: This field references the clock_times table and captures the specific time of day (to the second) at which the mail-order order was placed. It supports analysis of order timing by hour, shift, or meal period. A small proportion of rows (~0.5%) have a null value, suggesting some orders lack a recorded time of placement.

Sample values: No non-null sample values available.

### mail_order_purchases.sales_price

Short: The actual per-unit price charged to the customer for the product in this mail-order line item, after discounts.

Long: This field records the per-unit price actually charged to the customer after any discounts or promotions have been applied, ranging from $0.00 to $297.83. A value of $0.00 indicates a fully discounted or complimentary item. Comparing sales_price to list_price shows the effective discount per unit. A small proportion of rows (~0.5%) have a null value.

Sample values: No non-null sample values available.

### mail_order_purchases.shipping_address_ref

Short: The delivery address for the mail-order shipment, linking to the addresses table.

Long: This field references the addresses table and identifies the physical address to which the mail-order order was delivered. It enables geographic analysis of mail-order shipment destinations by city, state, ZIP code, or country. It may differ from billing_address_ref when orders are shipped to a different location than the billing address. A small proportion of rows (~0.5%) have a null value.

Sample values: No non-null sample values available.

### mail_order_purchases.shipping_calendar_day_ref

Short: The calendar date when the mail-order purchase was shipped, linking to the calendar_days reference table.

Long: This field references the calendar_days table and records the date on which the mail-order order was dispatched for delivery. It can be compared with sale_calendar_day_ref to calculate order fulfillment lead time or shipping lag. A small proportion of rows (~0.5%) have a null value, indicating some orders lack a recorded ship date.

Sample values: No non-null sample values available.

### mail_order_purchases.shipping_client_profile_ref

Short: The demographic profile of the shipping recipient at the time of the mail-order purchase, linking to the client_profiles table.

Long: This field references the client_profiles table and captures the demographic segment of the client who received the shipment. When the shipping and billing clients are the same person, this will match billing_client_profile_ref. When they differ (e.g., gift orders), this reflects the recipient's profile. It supports demographic analysis of mail-order delivery recipients.

Sample values: No non-null sample values available.

### mail_order_purchases.shipping_client_ref

Short: The customer account receiving the mail-order shipment, linking to the clients table.

Long: This field references the clients table and identifies the shopper to whom the mail-order order was shipped. In gift or third-party delivery scenarios, this may differ from the billing_client_ref. It is used to analyze delivery destinations and shipping behavior at the customer level. A small proportion of rows (~0.5%) have a null value.

Sample values: No non-null sample values available.

### mail_order_purchases.shipping_household_profile_ref

Short: The household segment of the shipping recipient at the time of the mail-order purchase, linking to the household_profiles table.

Long: This field references the household_profiles table and associates the shipping client's household segment with the mail-order transaction. It can be used to analyze the household characteristics of order recipients, which may differ from the billing household in gift or multi-household scenarios. A small proportion of rows (~0.5%) have a null value.

Sample values: No non-null sample values available.

### mail_order_purchases.support_center_ref

Short: The customer support or call center that handled the mail-order purchase, linking to the support_centers table.

Long: This field references the support_centers table and identifies which of the six support centers was associated with processing or assisting the mail-order order. Support center 1 handled the largest share of mail-order transactions. This field can be used to analyze order volume and revenue by support center, evaluate center performance, or study geographic distribution of assisted mail-order commerce. A small proportion of rows (~0.5%) have a null value.

Sample values: No non-null sample values available.

### mail_order_purchases.wholesale_cost

Short: The per-unit cost paid by the business to acquire the product for this mail-order line item.

Long: This field records the unit-level wholesale or acquisition cost of the merchandise item, ranging from $1.00 to $100.00. It represents what the business paid for the product, as opposed to what the customer paid. It is used to calculate gross margin and profitability when compared against sales price. A small proportion of rows (~0.5%) have a null value.

Sample values: No non-null sample values available.

## mail_order_refunds

This table records every item return or refund processed through the mail-order (catalog) channel. Each row represents a single return event and includes the date and time of the return, the product returned, the order it came from, and the quantity sent back. Two client roles are tracked: the client who was originally billed or refunded (refunded client) and the client who physically sent the item back (returning client). Both roles are linked to their demographic profiles and household profiles at the time of the return, enabling segmentation analysis. Financial columns cover the gross return amount, applicable taxes, processing fees, shipping costs for the return, and the breakdown of how the refund was issued—cash refund, charge reversal, or store credit. The net loss column summarizes the total financial impact of the return to the business. Support center, mailer page, delivery method, and fulfillment center references allow analysis of which operational units handled the return. Return reason links to a reference table describing why the item was sent back.

### mail_order_refunds.delivery_method_ref

Short: The shipping or delivery method used for the return.

Long: Links to the delivery_methods table to identify the carrier and service type used to ship the returned item back. All 20 available delivery methods appear with roughly equal frequency, suggesting no single carrier dominates return shipments. Approximately 2% of records have no value.

Sample values: No non-null sample values available.

### mail_order_refunds.fee

Short: A processing or restocking fee charged to the customer for the mail-order return.

Long: A fee deducted from the refund, potentially representing a restocking, handling, or administrative charge. Values range from $0.50 to $100.00. Approximately 2% of records have no value.

Sample values: No non-null sample values available.

### mail_order_refunds.fulfillment_center_ref

Short: The fulfillment center or warehouse that received the returned item.

Long: Links to the fulfillment_centers table to identify which of the five warehouse locations processed the incoming return. Volume is distributed roughly evenly across all five centers. Approximately 2% of records have no value.

Sample values: No non-null sample values available.

### mail_order_refunds.mailer_page_ref

Short: The catalog or mailer page associated with the returned item.

Long: Links to the mailer_pages table to identify the specific catalog page that featured the returned product. Enables analysis of which mailer editions or pages are associated with higher return rates. Approximately 2% of records have no value.

Sample values: No non-null sample values available.

### mail_order_refunds.merchandise_ref

Short: The product item that was returned in this mail-order refund.

Long: Links to the merchandise table to identify which product was sent back. Covers nearly all 18,000 products in the catalog, indicating returns are spread broadly across the product range. No missing values.

Sample values: No non-null sample values available.

### mail_order_refunds.net_loss

Short: The total financial loss to the business from this mail-order return.

Long: Summarizes the net financial impact of the return on the business, accounting for the refund amount, fees, and delivery costs. Higher values indicate more costly returns. Ranges from $0.50 to nearly $13,000. Approximately 2% of records have no value.

Sample values: No non-null sample values available.

### mail_order_refunds.order_number

Short: The mail-order order number associated with this return.

Long: Identifies the original mail-order purchase order that the returned item belongs to. Can be used to join back to the mail_order_purchases table to match returns to their originating sales. No missing values.

Sample values: No non-null sample values available.

### mail_order_refunds.refunded_address_ref

Short: The mailing address of the customer who received the refund.

Long: Links to the addresses table to identify the billing or contact address of the refunded client. Supports geographic analysis of where refunds are being issued. Approximately 2% of records have no value.

Sample values: No non-null sample values available.

### mail_order_refunds.refunded_cash

Short: The portion of the refund paid back to the customer as cash.

Long: The dollar amount returned to the customer in the form of a cash payment or direct monetary refund. Many returns show zero cash refunded, suggesting the refund was issued through other means such as charge reversal or store credit. Approximately 2% of records have no value.

Sample values: No non-null sample values available.

### mail_order_refunds.refunded_client_profile_ref

Short: The demographic profile of the customer who received the refund.

Long: Links to the client_profiles table to capture the demographic segment (gender, marital status, education, credit rating) of the refunded client at the time of the return. Useful for analyzing which customer segments generate the most refund activity. Approximately 2% of records have no value.

Sample values: No non-null sample values available.

### mail_order_refunds.refunded_client_ref

Short: The customer who received the refund payment for the returned mail-order item.

Long: Links to the clients table to identify the shopper who was financially credited for the return—typically the original purchaser or billing account. A small proportion of records (approximately 2%) have no value, which may indicate anonymous or unresolved transactions.

Sample values: No non-null sample values available.

### mail_order_refunds.refunded_household_profile_ref

Short: The household profile of the customer who received the refund.

Long: Links to the household_profiles table to capture the household-level segment (income range, buying potential, dependents, vehicles) of the refunded client. Enables income and household-based return analysis. Approximately 2% of records have no value.

Sample values: No non-null sample values available.

### mail_order_refunds.return_amount

Short: The gross refund amount before tax for the returned mail-order items.

Long: The pre-tax dollar value of the merchandise being refunded. Ranges from zero to over $24,000. Some returns have a zero amount, which may indicate exchanges or non-monetary resolutions. Approximately 2% of records have no value.

Sample values: No non-null sample values available.

### mail_order_refunds.return_amount_with_tax

Short: The total refund amount including tax for the returned mail-order items.

Long: The combined refund value of the merchandise and applicable taxes. This is the gross amount the customer is owed before any fees or delivery costs are deducted. Ranges from zero to over $26,000. Approximately 2% of records have no value.

Sample values: No non-null sample values available.

### mail_order_refunds.return_calendar_day_ref

Short: The calendar date when the mail-order return was processed.

Long: Links to the calendar_days table to identify the specific date on which the mail-order return or refund was recorded. Used for time-based analysis such as return trends by day, week, month, quarter, or year. Covers approximately 2,055 distinct return dates with no missing values.

Sample values: No non-null sample values available.

### mail_order_refunds.return_clock_time_ref

Short: The time of day when the mail-order return was processed.

Long: Links to the clock_times table to capture the specific time of day the return was recorded. Enables analysis of return activity by hour, shift, or time period. No missing values are present.

Sample values: No non-null sample values available.

### mail_order_refunds.return_delivery_cost

Short: The shipping cost incurred to return the mail-order item.

Long: The cost of shipping the returned merchandise back to the fulfillment center. Some returns show zero delivery cost, which may indicate prepaid return labels or in-person drop-offs. Approximately 2% of records have no value.

Sample values: No non-null sample values available.

### mail_order_refunds.return_quantity

Short: The number of units returned in this mail-order refund transaction.

Long: Indicates how many units of the product were sent back. Values range from 1 to 100, with smaller quantities being most common. Approximately 2% of records have no value.

Sample values: No non-null sample values available.

### mail_order_refunds.return_reason_ref

Short: The reason the customer returned the mail-order item.

Long: Links to the return_reasons table to classify why the item was sent back. All 35 return reason codes appear with roughly equal frequency, suggesting a wide variety of return motivations. Approximately 2% of records have no value.

Sample values: No non-null sample values available.

### mail_order_refunds.return_tax

Short: The tax portion of the mail-order refund amount.

Long: The tax amount refunded to the customer as part of the return. A significant share of returns show zero tax, which may reflect tax-exempt transactions or items returned without tax recovery. Approximately 2% of records have no value.

Sample values: No non-null sample values available.

### mail_order_refunds.returning_address_ref

Short: The address of the customer who physically returned the item.

Long: Links to the addresses table to identify the location from which the return was initiated. Useful for geographic analysis of return origins. Approximately 2% of records have no value.

Sample values: No non-null sample values available.

### mail_order_refunds.returning_client_profile_ref

Short: The demographic profile of the customer who physically returned the item.

Long: Links to the client_profiles table to capture the demographic segment of the returning client at the time of the return. Useful for understanding which customer segments are most likely to initiate a return. Approximately 2% of records have no value.

Sample values: No non-null sample values available.

### mail_order_refunds.returning_client_ref

Short: The customer who physically sent the item back.

Long: Links to the clients table to identify the shopper who actually returned the merchandise. This may differ from the refunded client in cases where a gift recipient or household member returns an item. Approximately 2% of records have no value.

Sample values: No non-null sample values available.

### mail_order_refunds.returning_household_profile_ref

Short: The household profile of the customer who physically returned the item.

Long: Links to the household_profiles table to capture the household-level segment of the returning client. Supports analysis of return behavior by income band, buying potential, or household size. Approximately 2% of records have no value.

Sample values: No non-null sample values available.

### mail_order_refunds.reversed_charge

Short: The portion of the refund issued as a charge reversal or credit card chargeback.

Long: The dollar amount credited back to the customer's payment method by reversing the original charge. Many records show zero, indicating this method is not always used. Approximately 2% of records have no value.

Sample values: No non-null sample values available.

### mail_order_refunds.store_credit

Short: The portion of the refund issued as store credit or account credit.

Long: The dollar amount returned to the customer in the form of store credit rather than cash or charge reversal. Some returns show zero store credit, indicating the refund was handled through other methods. Approximately 2% of records have no value.

Sample values: No non-null sample values available.

### mail_order_refunds.support_center_ref

Short: The customer support center that handled this mail-order return.

Long: Links to the support_centers table to identify which of the six support or call center locations processed the return. Useful for analyzing return volume and workload by support center. Approximately 2% of records have no value.

Sample values: No non-null sample values available.

## mailer_pages

This table describes individual pages within printed catalogs or mailers distributed to customers. Each record represents one page within a specific mailer issue and includes the mailer number (which catalog edition) and the page number within that mailer. Date references indicate when the mailer was active—its start and end calendar days—allowing analysis of which catalog editions drove purchases or returns during a given period. The department field categorizes the page by merchandise department, though the current data shows a placeholder value, suggesting this field may not yet be fully populated. The description field provides a free-text summary of the page content. The type field classifies the mailer cadence as monthly, quarterly, or bi-annual, which is useful for understanding promotion frequency and seasonality. This table is used as a dimension in mail-order purchase and refund analysis to connect transactions back to the specific catalog page that influenced the sale or return.

### mailer_pages.department

Short: The merchandise department featured on this mailer page.

Long: Indicates which product department is represented on the catalog page. The current data shows only a placeholder value ('DEPARTMENT') for nearly all records, suggesting this field may not yet contain meaningful department names. Interpret with caution until the data is fully populated. A small number of records have no value.

Sample values: No non-null sample values available.

### mailer_pages.description

Short: A free-text description of the content featured on this mailer page.

Long: A narrative description of what appears on the catalog page, such as the products, offers, or themes featured. Nearly all 11,718 pages have a unique description. This field can support keyword-based searches for specific product types or promotional themes within catalog pages. A small number of records have no value.

Sample values: No non-null sample values available.

### mailer_pages.end_calendar_day_ref

Short: The last date the mailer or catalog containing this page was active.

Long: Links to the calendar_days table to indicate when the mailer edition expired or was no longer in circulation. Together with the start date, this defines the active window for each catalog edition. A small number of records have no value.

Sample values: No non-null sample values available.

### mailer_pages.mailer_number

Short: The catalog or mailer edition number this page belongs to.

Long: Identifies which specific mailer or catalog issue the page is part of. Values range from 1 to 109, representing up to 109 distinct mailer editions. Used to group pages by catalog issue for edition-level analysis. A small number of records have no value.

Sample values: No non-null sample values available.

### mailer_pages.mailer_page_code

Short: Business-facing code that uniquely identifies a mailer page.

Long: An alphanumeric code assigned to each catalog or mailer page, serving as the business-facing identifier for the page. Each code is unique across all 11,718 mailer page records. Used to reference specific pages in purchase and return transactions.

Sample values: No non-null sample values available.

### mailer_pages.mailer_page_number

Short: The page number within the catalog or mailer.

Long: Indicates the physical page position within a given mailer edition. Values range from 1 to 108, representing the page sequence within a catalog. Used alongside the mailer number to pinpoint exactly which page of which edition is being referenced. A small number of records have no value.

Sample values: No non-null sample values available.

### mailer_pages.mailer_page_record

Short: Internal row identifier for each mailer page record.

Long: A unique sequential identifier for each row in the mailer_pages table. Each value appears exactly once, confirming it is a unique record key. This is an internal system identifier with no direct business meaning beyond uniquely identifying a catalog page record.

Sample values: No non-null sample values available.

### mailer_pages.start_calendar_day_ref

Short: The first date the mailer or catalog containing this page was active.

Long: Links to the calendar_days table to indicate when the mailer edition became available or was distributed to customers. Used to align catalog pages with the time periods during which they could have influenced purchases or returns. A small number of records have no value.

Sample values: No non-null sample values available.

### mailer_pages.type

Short: The publication frequency or cadence of the mailer containing this page.

Long: Classifies the mailer edition by how frequently it is published: monthly, quarterly, or bi-annual. Monthly mailers are the most common, accounting for roughly two-thirds of all pages. This field supports analysis of return and purchase behavior by catalog cadence or promotion cycle. A small number of records have no value.

Sample values: No non-null sample values available.

## marketing_campaigns

The marketing_campaigns table contains one record per marketing campaign or promotional offer run by the business. Each campaign has a name, a start and end date, a budget cost, and flags indicating which channels were used to reach customers — such as direct mail, email, mailer, TV, radio, press, event, or profile-based targeting. Campaigns can be linked to a specific merchandise item being promoted. The table supports analysis of campaign reach, channel mix, promotional timing, and the relationship between marketing activity and sales or revenue outcomes. The purpose field is currently recorded as 'Unknown' for most campaigns, and the discount_active flag indicates whether a discount was active during the campaign. With 300 campaigns in the dataset, this table is the primary reference for marketing attribution across store, online, and mail-order purchase channels.

### marketing_campaigns.campaign_code

Short: Business-facing alphanumeric code that uniquely identifies each marketing campaign.

Long: A unique alphanumeric code assigned to each marketing campaign, serving as the business-facing identifier for the campaign. This code can be used to look up or reference a specific campaign across systems. It differs from campaign_record, which is an internal numeric key.

Sample values: No non-null sample values available.

### marketing_campaigns.campaign_name

Short: The name or label assigned to the marketing campaign.

Long: A short text label identifying the marketing campaign by name. The current dataset contains a limited set of name values (such as 'able', 'anti', 'bar', 'pri', etc.), which appear to be synthetic or abbreviated identifiers rather than descriptive campaign titles. Business users should treat this as the campaign's display name for grouping and filtering purposes.

Sample values: No non-null sample values available.

### marketing_campaigns.campaign_record

Short: Internal row identifier for each marketing campaign record.

Long: A unique numeric identifier assigned to each campaign row in the marketing_campaigns table. This is an internal system key used to uniquely identify each campaign record. Business users should use campaign_code or campaign_name to reference campaigns by their business-facing identifiers.

Sample values: No non-null sample values available.

### marketing_campaigns.channel_details

Short: Free-text description providing additional details about the campaign's channel or messaging approach.

Long: A free-text field containing narrative details about the campaign's channel strategy, messaging, or other contextual information. Values are unique per campaign and appear to be descriptive sentences. This field can be used for qualitative review of campaign intent or to search for specific campaign themes, though it is not structured for aggregation.

Sample values: No non-null sample values available.

### marketing_campaigns.channel_direct_mail

Short: Indicates whether this campaign used the direct mail channel (Y/N).

Long: A flag indicating whether the campaign was distributed through the direct mail channel. Values are 'Y' (yes, direct mail was used) or 'N' (no). Approximately half of campaigns in the dataset used direct mail, making this one of the more active channels represented. Useful for filtering campaigns by channel type or analyzing direct mail campaign performance.

Sample values: No non-null sample values available.

### marketing_campaigns.channel_email

Short: Indicates whether this campaign used the email channel (Y/N).

Long: A flag indicating whether the campaign was distributed via email. In the current dataset, all non-null values are 'N', suggesting email was not used as a campaign channel in this data. This field may be populated in future campaigns or reflect a channel not yet activated.

Sample values: No non-null sample values available.

### marketing_campaigns.channel_event

Short: Indicates whether this campaign used an event-based channel (Y/N).

Long: A flag indicating whether the campaign was associated with a live or sponsored event. In the current dataset, all non-null values are 'N', indicating event-based marketing was not used for these campaigns. This field supports event marketing analysis when applicable.

Sample values: No non-null sample values available.

### marketing_campaigns.channel_mailer

Short: Indicates whether this campaign used the catalog mailer channel (Y/N).

Long: A flag indicating whether the campaign was distributed through a catalog or mailer channel. In the current dataset, all non-null values are 'N', suggesting the mailer channel was not used for any of these campaigns. This field may be relevant for future analysis if mailer campaigns are added.

Sample values: No non-null sample values available.

### marketing_campaigns.channel_press

Short: Indicates whether this campaign used the press or print media channel (Y/N).

Long: A flag indicating whether the campaign was distributed through press or print media such as newspapers or magazines. In the current dataset, all non-null values are 'N', indicating press was not used as a campaign channel in this data.

Sample values: No non-null sample values available.

### marketing_campaigns.channel_profile

Short: Indicates whether this campaign used profile-based or targeted demographic channel (Y/N).

Long: A flag indicating whether the campaign was targeted using customer demographic or household profile data. In the current dataset, all non-null values are 'N', suggesting profile-based targeting was not applied to these campaigns. This field may be relevant for personalized or segmented campaign analysis.

Sample values: No non-null sample values available.

### marketing_campaigns.channel_radio

Short: Indicates whether this campaign used the radio channel (Y/N).

Long: A flag indicating whether the campaign included radio advertising. In the current dataset, all non-null values are 'N', indicating radio was not used as a campaign channel. This field supports multi-channel campaign analysis when radio campaigns are present.

Sample values: No non-null sample values available.

### marketing_campaigns.channel_tv

Short: Indicates whether this campaign used the television channel (Y/N).

Long: A flag indicating whether the campaign included television advertising. In the current dataset, all non-null values are 'N', indicating TV was not used as a campaign channel. This field supports multi-channel campaign analysis when TV campaigns are present.

Sample values: No non-null sample values available.

### marketing_campaigns.cost

Short: The total budget or spend associated with running this marketing campaign.

Long: Represents the monetary cost of the marketing campaign. In the current dataset, all non-null campaigns show a cost of $1,000.00, suggesting this may be a standardized or placeholder value. Analysts should verify whether this reflects actual campaign spend or a default entry. A small number of campaigns have a null cost.

Sample values: No non-null sample values available.

### marketing_campaigns.discount_active

Short: Indicates whether a discount was active during this campaign (Y/N).

Long: A flag showing whether a promotional discount was in effect during the campaign period. In the current dataset, all non-null values are 'N', suggesting no discounts were active for any of these campaigns. This field is intended to support analysis of discount-driven versus non-discount campaigns and their impact on sales and revenue.

Sample values: No non-null sample values available.

### marketing_campaigns.end_calendar_day_ref

Short: The calendar day when the campaign ended, linked to the calendar_days reference table.

Long: References the calendar_days table to identify the date on which the marketing campaign concluded. Used alongside start_calendar_day_ref to calculate campaign duration and to filter campaigns active within a given reporting window. A small number of campaigns have a null end date.

Sample values: No non-null sample values available.

### marketing_campaigns.merchandise_ref

Short: The product promoted by this campaign, linked to the merchandise table.

Long: References the merchandise table to identify the specific product or item that this campaign was designed to promote. Allows analysts to connect campaign activity to product-level sales performance, category promotions, or brand-specific marketing efforts. Some campaigns do not have an associated merchandise item and will show a null value here.

Sample values: No non-null sample values available.

### marketing_campaigns.purpose

Short: The stated purpose or objective of the marketing campaign.

Long: Describes the business objective or goal of the campaign. In the current dataset, all non-null values are recorded as 'Unknown', indicating that campaign purpose has not been consistently captured. This field may be more meaningful in future data or after data quality improvements. Analysts should note the limited utility of this field in its current state.

Sample values: No non-null sample values available.

### marketing_campaigns.response_target

Short: The target number of customer responses expected from this campaign.

Long: Indicates the intended response volume or goal for the campaign. In the current dataset, all non-null values are recorded as 1, which may indicate a placeholder or that response targeting is not yet fully populated. The business meaning of this field may be uncertain given the limited variation in values.

Sample values: No non-null sample values available.

### marketing_campaigns.start_calendar_day_ref

Short: The calendar day when the campaign began, linked to the calendar_days reference table.

Long: References the calendar_days table to identify the date on which the marketing campaign started. This enables time-based analysis such as identifying campaigns active during a specific period, seasonal campaign patterns, or campaign duration calculations. A small number of campaigns have a null start date.

Sample values: No non-null sample values available.

## merchandise

The merchandise table is the central product catalog for the business, containing up to 18,000 product records. Each record describes a sellable item with attributes including product name, description, brand, product class, category, manufacturer, size, color, formulation, units of measure, and container type. Pricing information includes the current retail price and wholesale cost. The table supports slowly changing history through record_effective_start and record_effective_end dates, allowing analysis of how product attributes or pricing changed over time. Categories span Books, Children, Electronics, Home, Jewelry, Men, Music, Shoes, Sports, and Women. Classes include detailed groupings such as kids, shirts, fragrances, swimwear, dresses, and many others. This table is joined to purchase, refund, stock level, and campaign tables to analyze sales performance, profitability, inventory, and promotional effectiveness by product, brand, category, or manufacturer.

### merchandise.brand

Short: The brand name of the product.

Long: The human-readable brand name associated with the merchandise item. The catalog contains over 700 distinct brand names. Brand is a key dimension for filtering and grouping products in sales, return, and inventory analysis. Examples include various 'amalg', 'importo', 'edu pack', 'exporti', and 'scholar' brand families.

Sample values: No non-null sample values available.

### merchandise.brand_code

Short: Numeric code identifying the product's brand, used for joining or grouping by brand.

Long: A numeric identifier for the brand associated with the merchandise item. This code links to the brand name and can be used to group products by brand for sales, margin, or inventory analysis. There are approximately 948 distinct brand codes in the catalog. Business users may prefer to use the brand column for readable brand names.

Sample values: No non-null sample values available.

### merchandise.category

Short: The top-level product category, such as Books, Electronics, Shoes, or Women.

Long: The highest-level classification of a merchandise item into one of 10 product categories: Books, Children, Electronics, Home, Jewelry, Men, Music, Shoes, Sports, and Women. Category is a primary dimension for analyzing sales performance, returns, inventory, and marketing effectiveness across the product catalog. Products are distributed relatively evenly across categories.

Sample values: No non-null sample values available.

### merchandise.category_code

Short: Numeric code identifying the product's top-level category.

Long: A numeric identifier for the product's top-level category. There are 10 distinct category codes corresponding to the 10 product categories in the catalog. Business users may prefer to use the category column for readable category names. Category codes support efficient filtering and joining in category-level analysis.

Sample values: No non-null sample values available.

### merchandise.class

Short: The product class or sub-grouping, such as kids, shirts, fragrances, or swimwear.

Long: A descriptive label for the product's class — a sub-grouping within a product category. The catalog contains approximately 99 distinct class values, including apparel classes (kids, shirts, dresses, swimwear, mens, womens, toddlers, infants), music genres (classical, pop, country, rock), and other groupings (fragrances, accessories, athletic, school-uniforms). Class is a useful dimension for product-level sales and inventory analysis.

Sample values: No non-null sample values available.

### merchandise.class_code

Short: Numeric code identifying the product's class or sub-grouping within a category.

Long: A numeric identifier for the product class, which represents a sub-grouping of merchandise within a broader category. There are 16 distinct class codes. Business users may prefer to use the class column for readable class names. Class codes support efficient filtering and joining when analyzing product performance at the class level.

Sample values: No non-null sample values available.

### merchandise.color

Short: The color of the product.

Long: Describes the color of the merchandise item. The catalog contains approximately 92 distinct color values, including common colors such as turquoise, smoke, slate, peach, salmon, yellow, and many others. Color is a useful attribute for filtering products in apparel, home goods, and other color-relevant categories, and can be used in combination with size and class for detailed product analysis.

Sample values: No non-null sample values available.

### merchandise.container

Short: The container type for the product packaging.

Long: Describes the type of container or packaging used for the merchandise item. In the current dataset, all non-null values are recorded as 'Unknown', indicating that container information has not been populated. This field may be more meaningful in future data or for specific product categories where packaging type is relevant.

Sample values: No non-null sample values available.

### merchandise.current_price

Short: The current retail selling price of the product.

Long: The retail price at which the product is currently offered for sale to customers. Prices range from $0.09 to $99.99 across the catalog. This field reflects the list price for the active version of the product record and can be compared to wholesale_cost to assess margin. A small number of records have a null price.

Sample values: No non-null sample values available.

### merchandise.formulation

Short: A product formulation or specification code, likely encoding color or composition details.

Long: A text field containing what appears to be a product formulation or specification identifier. Values in the dataset are alphanumeric strings that appear to encode color names (such as pink, rose, yellow, salmon, lavender) combined with numeric sequences. The exact business meaning is uncertain, but this field may represent a product variant specification or internal formulation code used in manufacturing or sourcing.

Sample values: No non-null sample values available.

### merchandise.manager_code

Short: Numeric code identifying the manager responsible for this product or product group.

Long: A numeric identifier linking the merchandise item to a product manager or category manager. There are 100 distinct manager codes, suggesting a team of product managers each responsible for a portion of the catalog. This field can be used to analyze product performance, sales, or inventory by manager responsibility. The readable manager name is not available in this table.

Sample values: No non-null sample values available.

### merchandise.manufacturer

Short: The name of the company that manufactures the product.

Long: The human-readable name of the manufacturer responsible for producing the merchandise item. The catalog contains approximately 989 distinct manufacturer names. Manufacturer names in this dataset appear to be synthetic combinations of word fragments. This field supports vendor-level analysis of sales volume, product range, and profitability.

Sample values: No non-null sample values available.

### merchandise.manufacturer_code

Short: Numeric code identifying the product's manufacturer.

Long: A numeric identifier for the manufacturer of the merchandise item. There are approximately 994 distinct manufacturer codes in the catalog. Business users may prefer to use the manufacturer column for readable manufacturer names. Manufacturer codes support efficient filtering and joining in supplier or vendor analysis.

Sample values: No non-null sample values available.

### merchandise.merchandise_code

Short: Business-facing alphanumeric code identifying a product, shared across historical versions of the same item.

Long: An alphanumeric code that serves as the business-facing identifier for a merchandise item. Unlike merchandise_record, which is unique per row, merchandise_code may appear on multiple rows when a product has had multiple historical versions (due to price changes or attribute updates). Use this code to group all versions of the same product together for trend analysis.

Sample values: No non-null sample values available.

### merchandise.merchandise_description

Short: A text description of the product, providing additional detail beyond the product name.

Long: A free-text field containing a narrative description of the merchandise item. Descriptions vary in length and detail. This field can be used for keyword search or qualitative review of product attributes, though it is not structured for aggregation. A small number of records have a null description.

Sample values: No non-null sample values available.

### merchandise.merchandise_record

Short: Internal row identifier for each product record in the merchandise catalog.

Long: A unique numeric identifier assigned to each row in the merchandise table. Because the table supports slowly changing history (multiple versions of a product over time), a single product item may have more than one merchandise_record value across different effective date ranges. This is an internal system key; business users should use merchandise_code to identify products across time periods.

Sample values: No non-null sample values available.

### merchandise.product_name

Short: The display name of the product as it appears in the catalog.

Long: The human-readable name of the merchandise item as used in the product catalog. Product names in this dataset appear to be synthetic combinations of word fragments, with nearly 18,000 distinct names across the catalog. This is the primary field for identifying a product by name in search, reporting, and customer-facing contexts. A small number of records have a null product name.

Sample values: No non-null sample values available.

### merchandise.record_effective_end

Short: The date on which this version of the product record was superseded or expired.

Long: Indicates the calendar date on which this version of the merchandise record was replaced by a newer version. A null value in this field means the record is currently active (the most recent version). Approximately half of all records have a null end date, representing the current active product catalog. Use this field with record_effective_start to filter for the active product version at a given point in time.

Sample values: No non-null sample values available.

### merchandise.record_effective_start

Short: The date from which this version of the product record became active.

Long: Indicates the calendar date on which this particular version of the merchandise record became effective. Used in conjunction with record_effective_end to identify the active version of a product at any point in time. The dataset contains records starting as early as October 1997, reflecting a slowly changing product catalog.

Sample values: No non-null sample values available.

### merchandise.size

Short: The size of the product, such as small, medium, large, or N/A for non-sized items.

Long: Describes the physical size of the merchandise item. Values include small, medium, large, extra large, petite, economy, and N/A (for products where size is not applicable). The majority of products are classified as N/A, with medium being the most common sized option. Size is relevant for apparel, footwear, and similar categories.

Sample values: No non-null sample values available.

### merchandise.units

Short: The unit of measure in which the product is sold, such as Each, Box, Ounce, or Dozen.

Long: Describes the unit of measure used when selling the merchandise item. The catalog includes 21 distinct unit values such as Each, Box, Carton, Ounce, Oz, Dozen, Cup, Gram, Pound, Ton, Bundle, and others. Some products are marked as 'Unknown' or 'N/A' when the unit of measure is not defined. This field is relevant for quantity-based analysis and inventory management.

Sample values: No non-null sample values available.

### merchandise.wholesale_cost

Short: The cost paid by the business to acquire or produce the product.

Long: The wholesale or cost-of-goods price for the merchandise item — the amount the business pays to source or manufacture the product. Ranges from $0.02 to $87.36. Comparing wholesale_cost to current_price or sales_price in purchase tables enables gross margin and profitability analysis at the product level.

Sample values: No non-null sample values available.

## online_properties

The online_properties table contains one record per website or digital commerce property operated by the business. It supports online-channel analysis by capturing the site name, opening and closure dates, responsible manager, market grouping, and company affiliation. Address fields (street, city, state, ZIP, country) describe the registered or operational location of each digital property. The table uses slowly changing dimension logic, with record_effective_start and record_effective_end dates tracking historical versions of each property's attributes. A null record_effective_end indicates the currently active version. Tax percentage is stored at the property level to support revenue and tax calculations for online purchases. With 30 rows covering 5 distinct site names, this is a small reference dimension used to group and filter online purchase and refund activity by website, market region, or company.

### online_properties.city

Short: City where the online property's registered address is located.

Long: The city associated with the online property's registered or operational address. In the current dataset, all 30 properties are located in either Fairview or Midway, suggesting a concentrated geographic footprint for the digital channel's administrative offices.

Sample values: No non-null sample values available.

### online_properties.class

Short: Classification or tier of the online property. Currently shows 'Unknown' for all records.

Long: Intended to categorize the online property into a class or tier (similar to how retail locations are classified). However, all 30 records in the current dataset carry the value 'Unknown', so this field does not currently provide meaningful segmentation. Its business meaning is uncertain based on available evidence.

Sample values: No non-null sample values available.

### online_properties.closure_calendar_day_ref

Short: Reference to the calendar day when the online property closed or was decommissioned. Null if still active.

Long: Links to the calendar_days table to identify the date on which the website or digital property was closed or taken offline. A null value indicates the property is still operational. Approximately 17% of records have a closure date, suggesting most properties in the dataset remain active. Use this field to exclude closed sites from current-period analysis or to study site lifecycle patterns.

Sample values: No non-null sample values available.

### online_properties.company_code

Short: Numeric code identifying the company or business entity that owns the online property.

Long: A numeric identifier linking the online property to one of 6 company entities. This field supports analysis of online channel performance grouped by owning company or corporate division. It corresponds to the company_name field in the same record.

Sample values: No non-null sample values available.

### online_properties.company_name

Short: Name of the company or business entity that owns the online property.

Long: The name of the company that operates the online property. There are 6 distinct company names in the dataset (e.g., 'able', 'anti', 'cally', 'ese', 'ought', 'pri'). These appear to be abbreviated or synthetic names in the sample data. Use this field alongside company_code to group online sales, revenue, and refund metrics by owning company.

Sample values: No non-null sample values available.

### online_properties.country

Short: Country where the online property's address is located.

Long: The country associated with the online property's registered address. All 30 records show 'United States', indicating that all online properties in this dataset are US-based. This field may be relevant for multi-country expansion analysis in future data.

Sample values: No non-null sample values available.

### online_properties.county

Short: County where the online property's address is located.

Long: The county associated with the online property's registered address. All 30 records in the current dataset are located in Williamson County, indicating that all online properties share the same county location in this dataset.

Sample values: No non-null sample values available.

### online_properties.manager

Short: Name of the manager responsible for the online property or website.

Long: The full name of the individual who manages the online property. With 21 distinct manager names across 30 properties, some managers oversee more than one site. This field can be used to filter or group website performance metrics—such as online sales, revenue, or refund rates—by the responsible manager.

Sample values: No non-null sample values available.

### online_properties.market_class

Short: Descriptive classification label for the market region associated with the online property.

Long: A text description that classifies the market region in which the online property operates. The values appear to be free-form descriptive phrases. There are 23 distinct values across 30 records. This field provides additional context about the nature or characteristics of the market, though the sample values appear to be synthetic placeholder text and the precise business taxonomy is uncertain.

Sample values: No non-null sample values available.

### online_properties.market_code

Short: Numeric code identifying the market region the online property belongs to.

Long: A numeric identifier that groups online properties into market regions. There are 6 distinct market codes across the 30 properties. This field links conceptually to market-level attributes such as market_class, market_description, and market_manager stored in the same record. Use it to aggregate online channel performance by market region.

Sample values: No non-null sample values available.

### online_properties.market_description

Short: Longer narrative description of the market region for the online property.

Long: A free-text field providing a more detailed description of the market region associated with the online property. With 27 distinct values across 30 records, most markets have unique descriptions. The sample values appear to be synthetic placeholder text, so the precise business meaning of individual descriptions is uncertain. This field may be used for market-level reporting or filtering in conjunction with market_code and market_class.

Sample values: No non-null sample values available.

### online_properties.market_manager

Short: Name of the manager responsible for the market region the online property belongs to.

Long: The full name of the individual who manages the broader market region in which the online property operates. This is distinct from the property-level manager field; market_manager operates at a higher organizational level. With 24 distinct names across 30 records, most market managers oversee one or two properties. Use this field to roll up online channel performance to the market management level.

Sample values: No non-null sample values available.

### online_properties.name

Short: Display name of the online property or website.

Long: The human-readable name assigned to the online property, such as a site label (e.g., site_0 through site_4 in the sample data). This field identifies which website or digital channel a record belongs to and is useful for grouping online purchases, refunds, and revenue by site name.

Sample values: No non-null sample values available.

### online_properties.online_property_code

Short: Business-facing code that identifies a specific online property or website across its historical record versions.

Long: A alphanumeric code that serves as the business identifier for an online property. Unlike online_property_record, which is unique per row, online_property_code groups all historical versions of the same website together. Use this field to track a site across time or to join to other tables that reference the online property by its business code.

Sample values: No non-null sample values available.

### online_properties.online_property_record

Short: Internal row identifier for each online property record version. Used as a primary key to join online purchases and refunds.

Long: A unique numeric identifier assigned to each row in the online_properties table. Because the table uses slowly changing dimension logic, the same website may have multiple rows with different effective date ranges, each with its own online_property_record value. This is an internal system key; business users should use online_property_code or the site name to identify a specific website.

Sample values: No non-null sample values available.

### online_properties.opening_calendar_day_ref

Short: Reference to the calendar day when the online property first opened or launched.

Long: Links to the calendar_days table to identify the date on which the website or digital property was launched and began accepting online orders. This field supports analysis of site tenure, cohort comparisons by launch date, and filtering to properties that were active during a specific period.

Sample values: No non-null sample values available.

### online_properties.record_effective_end

Short: Date when this version of the online property record expired. Null indicates the currently active version.

Long: The calendar date on which this version of the online property's attributes was superseded by a newer record. A null value means the row represents the current, active version of the property. Approximately half of all rows have a null end date, indicating they are the live records. Use this field alongside record_effective_start to perform point-in-time or current-state analysis of website properties.

Sample values: No non-null sample values available.

### online_properties.record_effective_start

Short: Date when this version of the online property record became active.

Long: The calendar date on which this particular version of the online property's attributes took effect. Together with record_effective_end, this field defines the validity window for the row. When analyzing current website attributes, filter to rows where record_effective_start is on or before today and record_effective_end is null or after today.

Sample values: No non-null sample values available.

### online_properties.state

Short: US state where the online property's address is located.

Long: The US state abbreviation for the online property's registered address. All 30 records show 'TN' (Tennessee), consistent with the city and county data indicating all online properties are registered in the same state in this dataset.

Sample values: No non-null sample values available.

### online_properties.street_name

Short: Street name of the online property's registered or operational address.

Long: The name of the street in the online property's registered address. All 30 properties have distinct street names, suggesting each property has a unique physical address on record. This field is part of the full address used for location-based analysis or regulatory purposes.

Sample values: No non-null sample values available.

### online_properties.street_number

Short: Street number of the online property's registered or operational address.

Long: The numeric portion of the street address for the online property's registered location. Together with street_name, street_type, suite_number, city, state, ZIP, and country, this field forms the full mailing address of the digital property's associated office or operational site.

Sample values: No non-null sample values available.

### online_properties.street_type

Short: Street type suffix for the online property's address, such as Avenue, Boulevard, or Drive.

Long: The street type or suffix component of the online property's address (e.g., Avenue, Blvd, Drive, Road, Parkway). This field completes the street address alongside street_number and street_name. It is primarily used for address formatting and location identification rather than analytical segmentation.

Sample values: No non-null sample values available.

### online_properties.suite_number

Short: Suite or unit number within the building at the online property's address.

Long: The suite or office unit designation within the building at the online property's registered address. All 30 records have a suite number, indicating these properties are located in multi-tenant office buildings. This field is used for complete address formatting and mail delivery purposes.

Sample values: No non-null sample values available.

### online_properties.tax_percentage

Short: Sales tax rate applied to purchases made through this online property.

Long: The sales tax rate, expressed as a decimal percentage, applicable to transactions processed through the online property. Values range from 0.00 to 0.12 (0% to 12%), with 0.07 (7%) being the most common rate across 10 of the 30 properties. This field is used to calculate tax amounts on online purchases and supports revenue and tax reporting by website or market region.

Sample values: No non-null sample values available.

### online_properties.timezone_offset

Short: UTC timezone offset for the online property's location, used for time-of-day analysis.

Long: The numeric offset from UTC (Coordinated Universal Time) for the online property's registered location. All 30 records carry a value of -5.00, corresponding to Eastern Standard Time (UTC-5), consistent with the Tennessee address data. This field supports time-of-day analysis when correlating online purchase timestamps across time zones.

Sample values: No non-null sample values available.

### online_properties.zip

Short: Postal ZIP code for the online property's registered address.

Long: The postal ZIP code associated with the online property's registered address. Only two ZIP codes appear in the dataset (31904 and 35709), consistent with the limited number of cities. This field can be used for geographic filtering or tax jurisdiction identification.

Sample values: No non-null sample values available.

## online_purchases

The online_purchases table records every line-item sale completed through the business's website or digital commerce properties. With nearly 720,000 rows, it captures the full financial picture of each online order: the product sold, the customer who placed and received the order, the website and page where the purchase originated, the marketing campaign associated with the sale, the delivery method chosen, and the fulfillment center that shipped the goods. Financial columns cover unit-level pricing (wholesale cost, list price, sales price) as well as extended amounts scaled by quantity, including discounts, taxes, delivery costs, coupon savings, and multiple net-paid totals. A net profit column allows margin analysis at the transaction level. Dimension references connect each purchase to calendar dates for both sale and shipping timing, client demographic and household profiles for both the billing and shipping parties, and their respective addresses. This table is the primary source for online channel revenue, discount, coupon, delivery cost, and profitability analysis.

### online_purchases.billing_address_ref

Short: The billing address associated with this online purchase.

Long: Links the purchase to the billing customer's address record in the addresses table. Enables geographic analysis of online buyers by city, state, ZIP code, or country based on their billing location.

Sample values: No non-null sample values available.

### online_purchases.billing_client_profile_ref

Short: The demographic profile of the billing customer at the time of purchase.

Long: Links the billing customer to their demographic segment record in the client_profiles table, capturing attributes such as gender, marital status, education, and credit rating as they were at the time of the sale. Enables segmentation of online revenue by customer demographics.

Sample values: No non-null sample values available.

### online_purchases.billing_client_ref

Short: The customer who was billed for this online purchase.

Long: Links the purchase to the shopper account responsible for payment, referencing the clients table. This is the billing party, who may differ from the shipping recipient. Described in business context as the client billed for the online purchase. Use this to analyze purchasing behavior, customer lifetime value, and repeat-buyer patterns.

Sample values: No non-null sample values available.

### online_purchases.billing_household_profile_ref

Short: The household profile of the billing customer at the time of purchase.

Long: Links the billing customer to their household segment record in the household_profiles table, which includes income range, buying potential, dependent count, and vehicle ownership. Supports analysis of online purchasing behavior by household economic segment.

Sample values: No non-null sample values available.

### online_purchases.campaign_ref

Short: The marketing campaign or promotion associated with this online purchase.

Long: Links the purchase to a marketing campaign record in the marketing_campaigns table. With 300 distinct campaigns, this reference enables analysis of which promotions, offers, or discounts drove online sales, and supports campaign ROI and response rate reporting.

Sample values: No non-null sample values available.

### online_purchases.coupon_amount

Short: The total coupon savings applied to this purchase line.

Long: The aggregate value of coupons redeemed against this line item. The majority of online purchases show a coupon amount of zero, indicating coupons are used selectively. When non-zero, this represents additional savings beyond any promotional discount already reflected in the sales price. Useful for analyzing coupon redemption rates and their impact on revenue.

Sample values: No non-null sample values available.

### online_purchases.delivery_method_ref

Short: The shipping or delivery method selected for this order.

Long: Links the purchase to a delivery method record in the delivery_methods table, identifying the carrier, service type, and contract used to ship the order. Supports analysis of delivery method preferences, associated costs, and their impact on customer choice and profitability.

Sample values: No non-null sample values available.

### online_purchases.extended_delivery_cost

Short: Total shipping or delivery cost charged for this purchase line.

Long: The aggregate delivery or shipping cost associated with the line item, covering the cost of transporting the goods to the customer. Some orders show zero delivery cost, indicating free shipping promotions or included delivery. Use this to analyze shipping cost distribution, free shipping uptake, and delivery cost impact on profitability.

Sample values: No non-null sample values available.

### online_purchases.extended_discount_amount

Short: Total discount applied across all units in this purchase line.

Long: The aggregate discount amount for the line item, calculated as the difference between the extended list price and the extended sales price across all units purchased. A value of zero indicates no discount was applied. Useful for measuring promotional depth and total savings given to customers on online orders.

Sample values: No non-null sample values available.

### online_purchases.extended_list_price

Short: Total list price value for all units in this purchase line before discounts.

Long: The aggregate standard retail value of all units in the line item, calculated as list_price multiplied by quantity. Comparing this to extended_sales_price shows the total discount given. Useful for measuring the face value of merchandise sold and the overall discount rate applied.

Sample values: No non-null sample values available.

### online_purchases.extended_sales_price

Short: Total revenue from this line item at the actual selling price.

Long: The total amount charged to the customer for all units in this purchase line at the discounted sales price (sales_price multiplied by quantity). This is the primary revenue figure before tax and delivery costs are added. Use this for online channel revenue reporting and discount impact analysis.

Sample values: No non-null sample values available.

### online_purchases.extended_tax

Short: Total sales tax charged on this purchase line.

Long: The aggregate tax amount applied to the line item. A large proportion of online purchases show zero tax, reflecting tax-exempt transactions or jurisdictions without sales tax obligations. Used to calculate total customer cost including tax and to support tax reporting.

Sample values: No non-null sample values available.

### online_purchases.extended_wholesale_cost

Short: Total wholesale cost for all units in this purchase line.

Long: The aggregate cost of goods for the line item, calculated as wholesale_cost multiplied by quantity. Represents the business's total acquisition cost for the units sold in this transaction. Used alongside extended_sales_price to calculate gross margin at the line level.

Sample values: No non-null sample values available.

### online_purchases.fulfillment_center_ref

Short: The warehouse that fulfilled and shipped this online order.

Long: Links the purchase to one of five fulfillment center records in the fulfillment_centers table, identifying which warehouse location picked, packed, and shipped the order. Useful for analyzing fulfillment center workload, regional shipping patterns, and operational efficiency.

Sample values: No non-null sample values available.

### online_purchases.list_price

Short: The standard retail price of the product before any discounts.

Long: The published or catalog price of the merchandise item at the unit level, before promotional discounts or coupons are applied. Ranges from approximately $1.01 to $300.00. Comparing list price to sales price reveals the discount depth applied to each transaction.

Sample values: No non-null sample values available.

### online_purchases.merchandise_ref

Short: The product sold in this online purchase.

Long: Links each online purchase line to a specific product record in the merchandise table. Covers all 18,000 products in the catalog. Use this reference to analyze which items, brands, categories, or product classes drive online sales volume and revenue. Described in business context as the merchandise item sold through the online purchase.

Sample values: No non-null sample values available.

### online_purchases.net_paid

Short: Net amount paid by the customer, excluding tax and delivery.

Long: The total amount the customer paid for this purchase line after discounts and coupons are applied, but before tax and delivery costs are added. Described in business context as the net amount paid by the shopper for the online purchase. This is a core revenue metric for online channel performance analysis.

Sample values: No non-null sample values available.

### online_purchases.net_paid_with_delivery

Short: Net amount paid by the customer including delivery costs but excluding tax.

Long: The total customer payment for this purchase line including shipping or delivery charges, but before tax is added. Calculated as net_paid plus extended_delivery_cost. Useful for analyzing the combined impact of product pricing and delivery fees on the total amount customers pay.

Sample values: No non-null sample values available.

### online_purchases.net_paid_with_delivery_tax

Short: Total amount paid by the customer including both delivery costs and tax.

Long: The fully loaded customer payment for this purchase line, incorporating the net sales amount, delivery costs, and applicable taxes. This represents the complete out-of-pocket cost to the shopper. Use this as the all-in revenue figure for customer-facing total cost analysis and comprehensive online channel revenue reporting.

Sample values: No non-null sample values available.

### online_purchases.net_paid_with_tax

Short: Net amount paid by the customer including sales tax.

Long: The total customer payment for this purchase line including applicable sales tax, but excluding delivery costs. Calculated as net_paid plus extended_tax. Useful for understanding the total tax-inclusive cost to the customer and for tax-inclusive revenue reporting.

Sample values: No non-null sample values available.

### online_purchases.net_profit

Short: Net profit or loss on this online purchase line after costs.

Long: The profit generated from this purchase line, calculated as the net revenue received minus the wholesale cost of goods. Can be negative when discounts, coupons, or delivery costs exceed the revenue collected, indicating a loss on the transaction. Ranges from approximately -$9,938 to +$18,865. Use this for online channel margin analysis, profitability by product or campaign, and identifying loss-making transactions.

Sample values: No non-null sample values available.

### online_purchases.online_property_ref

Short: The digital commerce website or online property where the purchase was made.

Long: Links the purchase to one of up to 30 online property records in the online_properties table, identifying the specific website or digital storefront through which the sale occurred. Enables channel-level analysis of online revenue, volume, and performance across different web properties.

Sample values: No non-null sample values available.

### online_purchases.order_number

Short: The order identifier grouping line items within the same online purchase.

Long: A business-facing order number that groups one or more line items belonging to the same online transaction. Multiple rows may share the same order number when a single order contains multiple products. Use this to aggregate order-level totals or count distinct orders placed through online channels.

Sample values: No non-null sample values available.

### online_purchases.quantity

Short: The number of units of the product purchased in this line item.

Long: The count of product units included in this purchase line, ranging from 1 to 100. Extended financial amounts such as extended_sales_price and extended_wholesale_cost are derived by multiplying unit-level prices by this quantity. Use this field to analyze volume sold per product or order.

Sample values: No non-null sample values available.

### online_purchases.sale_calendar_day_ref

Short: The calendar date on which the online purchase was made.

Long: Links each online purchase to a specific day in the calendar_days table, identifying when the sale occurred. Use this reference to analyze online sales trends by day, week, month, quarter, or year, and to compare performance across time periods. Described in business context as the calendar day when the online purchase occurred.

Sample values: No non-null sample values available.

### online_purchases.sale_clock_time_ref

Short: The time of day when the online purchase was placed.

Long: Links each online purchase to a specific second-level time record in the clock_times table, capturing the exact time the order was placed. Enables analysis of online shopping activity by hour, shift, or time of day to understand peak purchasing windows.

Sample values: No non-null sample values available.

### online_purchases.sales_price

Short: The actual per-unit price charged to the customer after discounts.

Long: The unit selling price actually charged to the shopper, reflecting any promotional discounts applied to the list price. Can be zero for fully discounted items. Use this alongside list_price and wholesale_cost to understand pricing strategy and margin at the unit level.

Sample values: No non-null sample values available.

### online_purchases.shipping_address_ref

Short: The delivery address where the order was shipped.

Long: Links the purchase to the shipping destination address in the addresses table. Enables geographic analysis of where online orders are delivered, including city, state, ZIP, and country breakdowns for shipment destination reporting.

Sample values: No non-null sample values available.

### online_purchases.shipping_calendar_day_ref

Short: The calendar date on which the order was shipped to the customer.

Long: Links each online purchase to the day the order was dispatched, as recorded in the calendar_days table. Useful for analyzing shipping lead times, fulfillment speed, and the gap between purchase date and shipment date.

Sample values: No non-null sample values available.

### online_purchases.shipping_client_profile_ref

Short: The demographic profile of the shipping recipient at the time of purchase.

Long: Links the shipping recipient to their demographic segment record in the client_profiles table. Captures attributes such as gender, marital status, and education for the person who received the order. Supports demographic analysis of delivery recipients.

Sample values: No non-null sample values available.

### online_purchases.shipping_client_ref

Short: The customer to whom the order was shipped.

Long: Links the purchase to the client record of the person who received the shipment, referencing the clients table. The shipping client may differ from the billing client, for example in gift purchases. Useful for analyzing delivery destinations and recipient demographics.

Sample values: No non-null sample values available.

### online_purchases.shipping_household_profile_ref

Short: The household profile of the shipping recipient at the time of purchase.

Long: Links the shipping recipient to their household segment record in the household_profiles table, including income range, buying potential, and household composition. Useful for understanding the economic profile of households receiving online orders.

Sample values: No non-null sample values available.

### online_purchases.site_page_ref

Short: The website page from which the purchase originated.

Long: Links the purchase to a specific page record in the site_pages table, identifying which web page the customer was on when the order was placed. With 60 distinct pages, this supports analysis of which site pages drive the most online sales volume and revenue.

Sample values: No non-null sample values available.

### online_purchases.wholesale_cost

Short: The per-unit cost the business paid for the product.

Long: The unit-level wholesale or acquisition cost of the merchandise item, representing what the business paid to source the product. Used as the basis for calculating gross margin and profitability. Ranges from $1.00 to $100.00 per unit.

Sample values: No non-null sample values available.

## online_refunds

The online_refunds table records every item return or refund processed through the website or digital commerce channel. Each row represents a single online return event and captures when the return occurred (date and time), which product was returned, and the two client roles involved: the client who was originally billed (refunded client) and the client who physically submitted the return (returning client). Both client roles are linked to their demographic profiles, household profiles, and addresses at the time of the return, enabling segmentation analysis by geography, income range, and household characteristics. The table also links to the website page associated with the return, the reason the item was returned, and the original order number. Financial columns cover the gross return amount, applicable taxes, total return amount with tax, any processing fees, shipping costs for the return, and the three forms of reimbursement issued: cash refund, reversed charge (credit card reversal), and account credit. The net_loss column summarizes the overall financial loss to the business from the return. This table is the primary source for online return rate analysis, refund method breakdowns, return reason reporting, and customer return behavior studies.

### online_refunds.account_credit

Short: The portion of the refund issued as store or account credit.

Long: The dollar amount returned to the customer in the form of an online account or store credit rather than cash or a charge reversal. Many returns show $0.00, indicating the refund was issued through another method. Values range from $0.00 to over $12,500. Approximately 4.5% of rows have no value recorded.

Sample values: No non-null sample values available.

### online_refunds.fee

Short: A processing or restocking fee charged to the customer for the return.

Long: A fee deducted from the refund, potentially representing a restocking, handling, or return processing charge. Values range from $0.50 to $100.00, suggesting a capped fee structure. Approximately 4.4% of rows have no value recorded.

Sample values: No non-null sample values available.

### online_refunds.merchandise_ref

Short: The product item that was returned.

Long: Links to the merchandise table to identify which product was returned in this online refund transaction. Supports return rate analysis by product, brand, category, or class. This column is always populated with no null values observed.

Sample values: No non-null sample values available.

### online_refunds.net_loss

Short: The net financial loss to the business from the online return.

Long: Summarizes the overall financial impact of the return on the business, accounting for the refund amount, fees, and delivery costs. All recorded values are positive (minimum $0.52), confirming this always represents a loss. Values range up to nearly $12,000. Used for profitability and loss analysis of online returns. Approximately 4.4% of rows have no value recorded.

Sample values: No non-null sample values available.

### online_refunds.order_number

Short: The order number associated with the original online purchase being returned.

Long: Identifies the original online order that this return is linked to. Can be used to join return records back to the online_purchases table to analyze return rates by order. This column is always populated with no null values observed.

Sample values: No non-null sample values available.

### online_refunds.refunded_address_ref

Short: The address of the client receiving the refund.

Long: Links to the addresses table to capture the billing or mailing address of the client being refunded. Enables geographic analysis of online refunds by city, state, ZIP, or country. Approximately 4.3% of rows have no value recorded.

Sample values: No non-null sample values available.

### online_refunds.refunded_cash

Short: The portion of the refund paid back to the customer as cash.

Long: The dollar amount returned to the customer in the form of a cash payment or direct deposit. Many returns show $0.00 in this field, indicating the refund was issued through another method such as a charge reversal or account credit. Approximately 4.5% of rows have no value recorded.

Sample values: No non-null sample values available.

### online_refunds.refunded_client_profile_ref

Short: The demographic profile of the client being refunded at the time of the return.

Long: Links to the client_profiles table to capture the demographic segment (gender, marital status, education, credit rating) of the client receiving the refund. Enables demographic analysis of who is being refunded for online returns. Approximately 4.4% of rows have no value recorded.

Sample values: No non-null sample values available.

### online_refunds.refunded_client_ref

Short: The customer who was originally billed and is receiving the refund.

Long: Links to the clients table to identify the shopper whose account was charged in the original online purchase and who is the recipient of the refund. This may differ from the returning client if someone other than the original buyer submitted the return. Approximately 4.4% of rows have no value recorded.

Sample values: No non-null sample values available.

### online_refunds.refunded_household_profile_ref

Short: The household profile of the client being refunded.

Long: Links to the household_profiles table to capture the household segment (income range, buying potential, dependents, vehicle count) of the client receiving the refund. Supports household-level analysis of online return behavior. Approximately 4.3% of rows have no value recorded.

Sample values: No non-null sample values available.

### online_refunds.return_amount

Short: The gross refund amount before tax for the returned items.

Long: The pre-tax dollar value of the merchandise being refunded. Values range from $0.00 to over $21,000, reflecting the variety of products returned. This is the base refund amount before taxes are added. Approximately 4.4% of rows have no value recorded.

Sample values: No non-null sample values available.

### online_refunds.return_amount_with_tax

Short: The total refund amount including tax.

Long: The combined refund value of the returned merchandise plus applicable taxes. This is the gross amount the customer is owed before any fees or delivery costs are considered. Values range from $0.00 to over $23,000. Approximately 4.4% of rows have no value recorded.

Sample values: No non-null sample values available.

### online_refunds.return_calendar_day_ref

Short: The calendar date on which the online return was processed.

Long: Links to the calendar_days table to identify the specific date the online return occurred. Used for time-series analysis of return volumes by day, week, month, quarter, or year. Approximately 4.5% of rows have no date recorded.

Sample values: No non-null sample values available.

### online_refunds.return_clock_time_ref

Short: The time of day when the online return was processed.

Long: Links to the clock_times table to capture the hour, minute, and second the return was submitted online. Enables analysis of return activity by time of day, shift, or peak period. Approximately 4.5% of rows have no time recorded.

Sample values: No non-null sample values available.

### online_refunds.return_delivery_cost

Short: The shipping or delivery cost associated with the return.

Long: The cost of shipping the returned item back, which may be charged to the customer or absorbed by the business. Values range from $0.00 to over $11,000, with many zero-cost returns indicating free return shipping in some cases. Approximately 4.4% of rows have no value recorded.

Sample values: No non-null sample values available.

### online_refunds.return_quantity

Short: The number of units returned in this online refund transaction.

Long: Records how many units of the product were returned. Values range from 1 to 100, with single-unit returns being most common. Used to calculate total return volume and average units per return. Approximately 4.4% of rows have no value recorded.

Sample values: No non-null sample values available.

### online_refunds.return_reason_ref

Short: The reason the item was returned.

Long: Links to the return_reasons table to capture the stated reason for the online return, such as defect, wrong item, or customer preference. There are 35 distinct reason codes, evenly distributed across the dataset. Essential for return reason analysis and product quality reporting. Approximately 4.4% of rows have no value recorded.

Sample values: No non-null sample values available.

### online_refunds.return_tax

Short: The tax portion of the refund amount.

Long: The tax amount refunded to the customer as part of the online return. A significant portion of returns have zero tax (tax-exempt transactions or tax-free items). Values range from $0.00 to approximately $1,834. Approximately 4.3% of rows have no value recorded.

Sample values: No non-null sample values available.

### online_refunds.returning_address_ref

Short: The address of the client who submitted the return.

Long: Links to the addresses table to capture the address of the shopper who physically submitted the online return. Enables geographic analysis of where returns are being initiated from. Approximately 4.4% of rows have no value recorded.

Sample values: No non-null sample values available.

### online_refunds.returning_client_profile_ref

Short: The demographic profile of the client who submitted the return.

Long: Links to the client_profiles table to capture the demographic segment of the shopper who initiated the return. Useful for comparing the demographic profile of the returner versus the original buyer. Approximately 4.3% of rows have no value recorded.

Sample values: No non-null sample values available.

### online_refunds.returning_client_ref

Short: The customer who physically submitted or initiated the return.

Long: Links to the clients table to identify the shopper who actually submitted the online return. This may be the same as or different from the refunded client, allowing analysis of cases where a third party returns an item on behalf of the original buyer. Approximately 4.5% of rows have no value recorded.

Sample values: No non-null sample values available.

### online_refunds.returning_household_profile_ref

Short: The household profile of the client who submitted the return.

Long: Links to the household_profiles table to capture the household segment of the shopper who initiated the return. Supports household-level segmentation of return behavior in the online channel. Approximately 4.5% of rows have no value recorded.

Sample values: No non-null sample values available.

### online_refunds.reversed_charge

Short: The portion of the refund issued as a credit card or charge reversal.

Long: The dollar amount returned to the customer by reversing the original credit card or payment charge. Many returns show $0.00, indicating the refund was issued as cash or account credit instead. Values range from $0.00 to nearly $15,000. Approximately 4.4% of rows have no value recorded.

Sample values: No non-null sample values available.

### online_refunds.site_page_ref

Short: The website page associated with the return transaction.

Long: Links to the site_pages table to identify which online page was involved in the return process. There are 60 distinct page values, suggesting a fixed set of website pages. Useful for analyzing which pages are most associated with return activity. Approximately 4.5% of rows have no value recorded.

Sample values: No non-null sample values available.

## retail_locations

This table contains one record per physical retail store (or store version over time), capturing everything needed to analyze the brick-and-mortar sales channel. Each row describes a store's name, size, staffing, operating hours, manager, and full mailing address including city, state, ZIP, county, and country. Market grouping attributes such as market code, market description, and market manager support regional and territory analysis. Division and company fields are present but currently show a single unknown value, suggesting they may not yet be populated. A slowly-changing-dimension pattern is evident: record_effective_start and record_effective_end dates track when each version of a store's attributes was active, and closure_calendar_day_ref records when a store closed. The tax_percentage field supports sales tax calculations by store location. With only 12 rows, this table represents a small chain of physical stores.

### retail_locations.city

Short: City where the retail store is located.

Long: The city name for the store's physical address. The sample data shows stores located in Midway and Fairview, supporting city-level geographic analysis of the retail network.

Sample values: No non-null sample values available.

### retail_locations.closure_calendar_day_ref

Short: Calendar day reference indicating when the store permanently closed, if applicable.

Long: Links to the calendar_days table to identify the date a retail store closed. Most stores (75% of records) have a null value here, meaning they remain open. When populated, this field supports analysis of store closures and their impact on sales and customer behavior.

Sample values: No non-null sample values available.

### retail_locations.company_code

Short: Numeric code identifying the company that owns the retail store.

Long: A numeric identifier for the parent company of the store. All records currently show a single company code of 1, consistent with a single-company retail operation or a field not yet differentiated.

Sample values: No non-null sample values available.

### retail_locations.company_name

Short: Name of the company that owns or operates the retail store.

Long: The name of the parent company for the store location. All current records show 'Unknown', suggesting this field is not yet populated with meaningful data. It is intended to support multi-company or franchise analysis.

Sample values: No non-null sample values available.

### retail_locations.country

Short: Country where the retail store is located.

Long: The country name for the store's physical address. All stores are located in the United States, consistent with a domestic retail operation.

Sample values: No non-null sample values available.

### retail_locations.county

Short: County where the retail store is located.

Long: The county name for the store's physical address. All stores in the sample are located in Williamson County, which may reflect the geographic scope of this retail network or synthetic data constraints.

Sample values: No non-null sample values available.

### retail_locations.division_code

Short: Numeric code identifying the business division the store belongs to.

Long: A numeric identifier for the organizational division that owns or operates the store. All current records show a single division code of 1, suggesting either a single-division business or that this field is not yet fully populated.

Sample values: No non-null sample values available.

### retail_locations.division_name

Short: Name of the business division the store belongs to.

Long: The name of the organizational division responsible for the store. All current records show 'Unknown', indicating this field may not yet be populated with meaningful data. Its intended use is organizational segmentation by division.

Sample values: No non-null sample values available.

### retail_locations.floor_space

Short: Physical size of the retail store, measured in square feet.

Long: The total floor area of the store location in square feet. Values range from roughly 5.2 million to 9.3 million square feet in the sample data, which appear unusually large and may reflect synthetic data. In practice, this field supports store size segmentation and sales-per-square-foot analysis.

Sample values: No non-null sample values available.

### retail_locations.geography_class

Short: Geographic classification of the store's market area.

Long: A categorical label describing the geographic type of the store's market, such as urban, suburban, or rural. All current records show 'Unknown', so this field does not currently provide meaningful segmentation. Its intended use is geographic market classification.

Sample values: No non-null sample values available.

### retail_locations.hours

Short: Operating hours of the retail store, expressed as a time range.

Long: The daily opening and closing hours for the store, such as '8AM-4PM' or '8AM-12AM'. Most stores in the sample operate the shorter shift. This field supports analysis of store accessibility and can be used to align sales timing with operating windows.

Sample values: No non-null sample values available.

### retail_locations.manager

Short: Name of the store manager responsible for the retail location.

Long: The full name of the individual managing the store. Used to attribute store performance to specific managers and to support operational reporting. Some managers oversee multiple store records, which may reflect multiple time periods or multiple locations.

Sample values: No non-null sample values available.

### retail_locations.market_code

Short: Numeric code identifying the market or territory the store belongs to.

Long: A numeric identifier grouping stores into market or geographic territory segments. Used alongside market_description and market_manager to analyze performance by region or sales territory. Seven distinct market codes appear across the 12 store records.

Sample values: No non-null sample values available.

### retail_locations.market_description

Short: Descriptive text explaining the market or territory associated with the store.

Long: A free-text description of the market segment or territory in which the store operates. The sample values appear to be synthetic placeholder text. In a production context, this field would describe the competitive landscape, customer base, or geographic characteristics of the market.

Sample values: No non-null sample values available.

### retail_locations.market_manager

Short: Name of the manager responsible for the store's market or sales territory.

Long: The full name of the individual overseeing the broader market or territory that includes this store. Distinct from the store-level manager, the market manager typically has responsibility across multiple store locations within a region.

Sample values: No non-null sample values available.

### retail_locations.number_employees

Short: Number of employees working at the retail store.

Long: The headcount of staff employed at the store location. Values range from approximately 218 to 297 across the 12 stores. This attribute can be used to compare store size, staffing levels, and productivity metrics such as sales per employee.

Sample values: No non-null sample values available.

### retail_locations.record_effective_end

Short: Date when this version of the store's attributes expired or was superseded.

Long: The end date of the period during which this store record was active. A null value indicates the record is the current active version of the store's attributes. Approximately half of all records are currently active (null end date).

Sample values: No non-null sample values available.

### retail_locations.record_effective_start

Short: Date when this version of the store's attributes became active.

Long: The start date of the period during which this store record's attributes (such as manager, hours, or address) were valid. Used together with record_effective_end to identify the current or historical version of a store's profile. Supports time-based analysis of store attribute changes.

Sample values: No non-null sample values available.

### retail_locations.retail_location_code

Short: Business-facing code that identifies a physical retail store.

Long: An alphanumeric code used to reference a specific retail store in business reporting and cross-table joins. Multiple rows may share the same code when a store's attributes have changed over time, reflecting the slowly-changing-dimension history of the table.

Sample values: No non-null sample values available.

### retail_locations.retail_location_record

Short: Internal system identifier uniquely identifying each retail store record row.

Long: A sequential numeric identifier assigned to each row in the retail_locations table. It serves as the internal primary key and is used to join store records to purchase and refund fact tables. Business users should use retail_location_code or store_name to identify stores by name.

Sample values: No non-null sample values available.

### retail_locations.state

Short: US state where the retail store is located.

Long: The two-letter US state abbreviation for the store's address. All stores in the sample are located in Tennessee (TN), supporting state-level geographic filtering and regional analysis.

Sample values: No non-null sample values available.

### retail_locations.store_name

Short: The name of the physical retail store.

Long: The display name used to identify a retail store location. Sample values in the data appear to be partial words, which may reflect synthetic or anonymized test data. In a production context, this field would contain the full store name used in business reporting and customer-facing communications.

Sample values: No non-null sample values available.

### retail_locations.street_name

Short: Street name component of the store's physical address.

Long: The name of the street where the retail store is located. Used together with street_number and street_type to construct the full street address for mapping, logistics, and geographic analysis.

Sample values: No non-null sample values available.

### retail_locations.street_number

Short: Street number component of the store's physical address.

Long: The numeric portion of the store's street address. Combined with street_name, street_type, and suite_number, it forms the complete street address of the retail location.

Sample values: No non-null sample values available.

### retail_locations.street_type

Short: Street type suffix for the store's address, such as Boulevard, Lane, or Court.

Long: The road type designation that follows the street name in the store's mailing address. Examples include Boulevard, Circle, Court, Drive, Lane, and Road. Used to complete the full street address.

Sample values: No non-null sample values available.

### retail_locations.suite_number

Short: Suite or unit number within the store's building address.

Long: The suite or unit identifier for the store's location within a building or complex. All store records include a suite designation, suggesting the stores are located within larger commercial properties.

Sample values: No non-null sample values available.

### retail_locations.tax_percentage

Short: Sales tax rate applied to purchases at the retail store.

Long: The local sales tax rate expressed as a decimal percentage (e.g., 0.03 = 3%). Rates vary across stores from 1% to 11%, reflecting different local tax jurisdictions. Used to calculate tax amounts on store purchases and to compare net revenue across locations.

Sample values: No non-null sample values available.

### retail_locations.timezone_offset

Short: UTC timezone offset for the store's location, used to align transaction timestamps.

Long: The numeric offset from UTC for the store's local time zone. All stores show an offset of -5.00, consistent with US Eastern Standard Time. Used to normalize transaction timestamps across locations when analyzing time-of-day patterns.

Sample values: No non-null sample values available.

### retail_locations.zip

Short: Postal ZIP code for the retail store's address.

Long: The five-digit US postal ZIP code for the store location. Two ZIP codes appear in the sample data, corresponding to the two cities (Midway and Fairview). Used for geographic segmentation and delivery zone analysis.

Sample values: No non-null sample values available.

## return_reasons

This small reference table contains 35 distinct return or refund reason codes and their plain-language descriptions. It is used across store, online, and mail-order refund transactions to explain why a shopper returned a product. Example reasons include 'Did not fit', 'Wrong size', 'Package was damaged', 'Found a better price in a store', 'Stopped working', 'Gift exchange', and 'Unauthorized purchase'. Each reason has a unique business-facing code and a human-readable description. This table is joined to refund fact tables to enable analysis of return drivers, product quality issues, pricing competitiveness, and fulfillment problems.

### return_reasons.reason_description

Short: Plain-language explanation of why a customer returned or refunded a product.

Long: A human-readable description of the return or refund reason. Examples include 'Did not fit', 'Wrong size', 'Package was damaged', 'Found a better price in a store', 'Stopped working', 'Gift exchange', 'Lost my job', and 'Unauthorized purchase'. This field is the primary attribute for analyzing return drivers, identifying product quality issues, understanding customer satisfaction, and benchmarking return rates by reason category.

Sample values: No non-null sample values available.

### return_reasons.return_reason_code

Short: Business-facing code that uniquely identifies a return or refund reason.

Long: An alphanumeric code used to reference a specific return reason in cross-table joins and business reporting. Each code corresponds to one reason_description. This code appears as a foreign key in store_refunds, online_refunds, and mail_order_refunds tables.

Sample values: No non-null sample values available.

### return_reasons.return_reason_record

Short: Internal system identifier uniquely identifying each return reason row.

Long: A sequential numeric identifier serving as the internal primary key for the return_reasons table. It is used to join return reason records to refund fact tables across store, online, and mail-order channels. Business users should reference return_reason_code or reason_description to identify specific return reasons.

Sample values: No non-null sample values available.

## site_pages

This table holds one record per website page (or page version over time), capturing attributes used to analyze the online shopping experience. Each page is described by its type (such as general, welcome, ad, order, feedback, dynamic, or protected), URL, creation date, most recent access date, and content metrics including character count, number of links, number of images, and maximum ad slots. An auto-generated flag distinguishes system-created pages from manually authored ones. A client reference links some pages to specific shoppers, though roughly 65% of pages have no client association, suggesting most pages are public or shared. The slowly-changing-dimension pattern (record_effective_start and record_effective_end) tracks page attribute changes over time. This table supports analysis of website content strategy, page engagement timing, and ad inventory capacity.

### site_pages.access_calendar_day_ref

Short: Calendar day reference indicating the most recent date the website page was accessed.

Long: Links to the calendar_days table to identify the last date the page was visited or accessed. Supports analysis of page recency, engagement timing, and identification of stale or inactive pages. All records have a non-null access date.

Sample values: No non-null sample values available.

### site_pages.auto_generated_flag

Short: Indicates whether the website page was automatically generated by the system.

Long: A yes/no flag (Y/N) distinguishing system-generated pages from manually authored ones. Approximately 35% of pages are auto-generated. This field supports content strategy analysis by separating programmatic pages (such as product listing pages) from hand-crafted editorial content.

Sample values: No non-null sample values available.

### site_pages.char_count

Short: Total number of characters of text content on the website page.

Long: A measure of the text volume on the page, expressed as a character count. Values range from approximately 700 to 7,000 characters. This field supports content analysis, such as identifying whether longer pages correlate with higher engagement or conversion rates.

Sample values: No non-null sample values available.

### site_pages.client_ref

Short: Links the website page to a specific customer account, when applicable.

Long: References the clients table to associate a page with a particular shopper or account. Approximately 65% of pages have no client association (null), suggesting most pages are public or shared across all visitors. When populated, this field may indicate personalized or account-specific pages such as order confirmation or profile pages.

Sample values: No non-null sample values available.

### site_pages.creation_calendar_day_ref

Short: Calendar day reference indicating when the website page was created.

Long: Links to the calendar_days table to identify the date the page was originally created or published. Supports analysis of page age, content freshness, and the timing of website expansion. A small number of records have a null creation date.

Sample values: No non-null sample values available.

### site_pages.image_count

Short: Number of images displayed on the website page.

Long: The total count of images on the page, ranging from 1 to 7. This field supports analysis of visual content richness and its potential impact on shopper engagement, conversion rates, and page load considerations.

Sample values: No non-null sample values available.

### site_pages.link_count

Short: Number of hyperlinks present on the website page.

Long: The total count of clickable links on the page. Values range from 2 to 25 links. This field can be used to analyze page navigation complexity, internal linking strategy, and the relationship between link density and shopper behavior.

Sample values: No non-null sample values available.

### site_pages.max_ad_count

Short: Maximum number of advertisements that can be displayed on the website page.

Long: The upper limit of ad slots available on the page, ranging from 0 to 4. Pages with 0 carry no advertising. This field supports ad inventory analysis, revenue potential estimation from digital advertising, and decisions about which pages to monetize with promotional placements.

Sample values: No non-null sample values available.

### site_pages.record_effective_end

Short: Date when this version of the website page's attributes expired or was superseded.

Long: The end date of the period during which this page record was active. A null value indicates the record is the current active version. Approximately half of all records are currently active (null end date), consistent with a slowly-changing-dimension pattern.

Sample values: No non-null sample values available.

### site_pages.record_effective_start

Short: Date when this version of the website page's attributes became active.

Long: The start date of the period during which this page record's attributes were valid. Used together with record_effective_end to identify the current or historical version of a page's profile. Supports time-based analysis of page content changes.

Sample values: No non-null sample values available.

### site_pages.site_page_code

Short: Business-facing code that identifies a specific website page.

Long: An alphanumeric code used to reference a website page in cross-table joins and reporting. Multiple rows may share the same code when a page's attributes have changed over time, reflecting the slowly-changing-dimension history of the table.

Sample values: No non-null sample values available.

### site_pages.site_page_record

Short: Internal system identifier uniquely identifying each website page record row.

Long: A sequential numeric identifier serving as the internal primary key for the site_pages table. It is used to join page records to online purchase and refund transactions. Business users should use site_page_code or url to reference specific pages.

Sample values: No non-null sample values available.

### site_pages.type

Short: Category or functional type of the website page, such as general, ad, order, or welcome.

Long: A classification label describing the purpose or function of the page. Observed values include 'general', 'welcome', 'protected', 'ad', 'dynamic', 'feedback', and 'order'. This field supports segmentation of web traffic and conversion analysis by page function, such as comparing engagement on ad pages versus order confirmation pages.

Sample values: No non-null sample values available.

### site_pages.url

Short: Web address (URL) of the website page.

Long: The full URL of the website page. In the sample data, all pages share the same placeholder URL, which likely reflects synthetic test data. In a production context, this field would contain distinct URLs used to identify and link to specific pages for traffic and conversion analysis.

Sample values: No non-null sample values available.

## stock_levels

This large fact-style table records the quantity of each merchandise item on hand at each fulfillment center for each calendar day. With nearly 12 million rows, it provides a comprehensive view of inventory availability across the warehouse network. Each row links a specific product (via merchandise_ref), a warehouse location (via fulfillment_center_ref), and a calendar date (via calendar_day_ref) to a quantity_on_hand figure ranging from 0 to 1,000. Approximately 5% of quantity_on_hand values are null, which may indicate missing snapshots or items not tracked on certain days. The table covers all 5 fulfillment centers and all 18,000 merchandise items, making it the primary source for inventory analysis, stockout detection, replenishment planning, and supply-demand comparisons against purchase volumes.

### stock_levels.calendar_day_ref

Short: Calendar day reference indicating the date of the inventory snapshot.

Long: Links to the calendar_days table to identify the specific date for which the stock quantity is recorded. Stock levels are captured at regular weekly intervals across a multi-year period. This field is essential for tracking inventory trends over time, identifying seasonal patterns, and comparing stock availability to purchase demand on specific dates.

Sample values: No non-null sample values available.

### stock_levels.fulfillment_center_ref

Short: Links the stock record to a specific warehouse or fulfillment center.

Long: References the fulfillment_centers table to identify which warehouse holds the recorded stock quantity. All 5 fulfillment centers are represented equally in the data. Used to analyze inventory distribution across the warehouse network and to support fulfillment planning and stockout analysis by location.

Sample values: No non-null sample values available.

### stock_levels.merchandise_ref

Short: Links the stock record to a specific product in the merchandise catalog.

Long: References the merchandise table to identify which product item the stock count applies to. All 18,000 merchandise items appear in the stock_levels table, enabling inventory analysis across the full product catalog. Used to join stock data with product attributes such as brand, category, and class.

Sample values: No non-null sample values available.

### stock_levels.quantity_on_hand

Short: Number of units of a product available in inventory at a fulfillment center on a given day.

Long: The count of product units physically available at a specific fulfillment center on a specific calendar day. Values range from 0 (out of stock) to 1,000 units. Approximately 5% of records have a null value, which may indicate missing data for certain product-location-date combinations. This is the primary metric for inventory availability analysis, stockout detection, replenishment planning, and supply-demand gap assessment.

Sample values: No non-null sample values available.

## store_purchases

Records every line-item sale completed at a physical store location. Each row captures the product sold, the shopper and their demographic profile, the store where the purchase occurred, the date and time of the sale, any marketing campaign associated with the transaction, and a full set of financial measures including wholesale cost, list price, actual sales price, discount amount, coupon savings, tax, and net profit. This table is the primary source for in-store revenue analysis, basket-level profitability, campaign effectiveness at retail, and customer purchase behavior segmented by demographics, household profile, or geography.

### store_purchases.address_ref

Short: The customer's address associated with this store purchase.

Long: Links the store purchase to a specific address record in the addresses dimension. Used to analyze in-store sales by customer geography, including city, state, ZIP code, and county.

Sample values: No non-null sample values available.

### store_purchases.campaign_ref

Short: The marketing campaign associated with this store purchase.

Long: Links the store purchase to a specific marketing campaign or promotional offer in the marketing_campaigns dimension. Used to measure campaign-driven sales, discount effectiveness, and promotional lift at the store level.

Sample values: No non-null sample values available.

### store_purchases.client_profile_ref

Short: The demographic profile of the customer at the time of purchase.

Long: Links the store purchase to a specific demographic segment record in the client_profiles dimension, capturing attributes such as gender, marital status, education, and credit rating as they were at the time of the transaction. Used for demographic segmentation of in-store sales.

Sample values: No non-null sample values available.

### store_purchases.client_ref

Short: The customer who made this in-store purchase.

Long: Links the store purchase to a specific shopper account in the clients dimension. Used to analyze purchase history, customer loyalty, and lifetime value for individual shoppers. May be null for anonymous or unidentified transactions.

Sample values: No non-null sample values available.

### store_purchases.coupon_amount

Short: Total coupon savings applied to this purchase line.

Long: The total value of coupons redeemed on this purchase line. Most transactions show zero coupon usage. Used to measure coupon redemption rates, promotional savings, and the impact of coupon campaigns on revenue.

Sample values: No non-null sample values available.

### store_purchases.extended_discount_amount

Short: Total discount applied across all units in this purchase line.

Long: The total monetary discount given on this purchase line, calculated across the full quantity purchased. Represents the difference between the extended list price and the extended sales price attributable to discounting. Frequently zero when no discount applies.

Sample values: No non-null sample values available.

### store_purchases.extended_list_price

Short: Total list price for this purchase line (quantity × list price).

Long: The total standard retail value of this purchase line before any discounts, equal to the list price per unit multiplied by the quantity. Used to measure the full value of items sold and the total discount given.

Sample values: No non-null sample values available.

### store_purchases.extended_sales_price

Short: Total sales revenue for this purchase line (quantity × sales price).

Long: The total amount charged to the customer for this purchase line, equal to the sales price multiplied by the quantity. This is the primary revenue measure at the line-item level before tax and after discounts.

Sample values: No non-null sample values available.

### store_purchases.extended_tax

Short: Total sales tax charged on this purchase line.

Long: The total tax amount applied to this purchase line. Many transactions carry zero tax, reflecting tax-exempt items or locations. Used to analyze tax liability and to reconcile net paid amounts with and without tax.

Sample values: No non-null sample values available.

### store_purchases.extended_wholesale_cost

Short: Total wholesale cost for this purchase line (quantity × wholesale cost).

Long: The total cost of goods for this purchase line, equal to the wholesale cost per unit multiplied by the quantity. Used to calculate gross profit and margin at the line-item level.

Sample values: No non-null sample values available.

### store_purchases.household_profile_ref

Short: The household profile of the customer at the time of purchase.

Long: Links the store purchase to a household-level segment record in the household_profiles dimension, capturing attributes such as income range, buying potential, number of dependents, and vehicle count. Used for household-level segmentation of in-store sales.

Sample values: No non-null sample values available.

### store_purchases.list_price

Short: The standard retail list price for one unit of the product.

Long: The published or catalog price per unit for the merchandise item before any discounts or promotions are applied. Used to measure discount depth and compare actual selling prices against the standard price.

Sample values: No non-null sample values available.

### store_purchases.merchandise_ref

Short: The product sold in this store purchase.

Long: Links each store purchase line to a specific product record in the merchandise dimension. Used to analyze which items are selling in stores, and to join product attributes such as brand, category, class, and price for product-level sales reporting.

Sample values: No non-null sample values available.

### store_purchases.net_paid

Short: Net amount paid by the customer for this purchase line, excluding tax.

Long: The total amount the shopper actually paid for this purchase line after discounts and coupons, but before tax. This is the primary revenue measure for store sales analysis. Defined in the business context as the net amount paid by the shopper for the store purchase.

Sample values: No non-null sample values available.

### store_purchases.net_paid_with_tax

Short: Net amount paid by the customer including sales tax.

Long: The total amount the shopper paid for this purchase line including applicable sales tax. Used when tax-inclusive revenue figures are needed for financial reporting or regional tax analysis.

Sample values: No non-null sample values available.

### store_purchases.net_profit

Short: Net profit or loss on this store purchase line.

Long: The profit generated on this purchase line, calculated as the net amount paid minus the wholesale cost. Can be negative when items are sold below cost, such as during deep promotions or clearance events. Used for profitability analysis by product, store, campaign, or customer segment.

Sample values: No non-null sample values available.

### store_purchases.quantity

Short: The number of units of the product purchased.

Long: The quantity of the merchandise item sold in this store purchase line. Values range from 1 to 100. Used to calculate total units sold, average basket size, and volume-based sales metrics.

Sample values: No non-null sample values available.

### store_purchases.retail_location_ref

Short: The physical store where this purchase took place.

Long: Links the store purchase to a specific retail location in the retail_locations dimension. Only six distinct store locations appear in the data. Used to compare sales performance, revenue, and product mix across store locations.

Sample values: No non-null sample values available.

### store_purchases.sale_calendar_day_ref

Short: The calendar date when the in-store purchase was made.

Long: Links each store purchase to a specific calendar day in the calendar_days dimension. Used to analyze sales trends by day, week, month, quarter, year, or fiscal period. This is the primary date dimension for store purchase timing analysis.

Sample values: No non-null sample values available.

### store_purchases.sale_clock_time_ref

Short: The time of day when the in-store purchase occurred.

Long: Links each store purchase to a specific time of day in the clock_times dimension. Enables analysis of purchase activity by hour, shift, or meal period, supporting questions about peak shopping times within a store.

Sample values: No non-null sample values available.

### store_purchases.sales_price

Short: The actual per-unit price charged to the customer.

Long: The price per unit actually charged to the shopper for this merchandise item, after any discounts or promotions. May be zero for fully discounted or promotional items. Used to analyze effective selling prices and discount impact.

Sample values: No non-null sample values available.

### store_purchases.ticket_number

Short: The sales receipt or transaction ticket identifier for this store purchase.

Long: A numeric identifier representing the sales ticket or receipt for the in-store transaction. Multiple line items may share the same ticket number, representing different products purchased in a single shopping trip. Used to group line items into basket-level transactions.

Sample values: No non-null sample values available.

### store_purchases.wholesale_cost

Short: The per-unit cost the business paid for the product.

Long: The unit-level wholesale or cost-of-goods amount for the merchandise item in this purchase. Used to calculate gross margin and profitability when compared against the sales price.

Sample values: No non-null sample values available.

## store_refunds

Records every item return or refund processed at a physical store location. Each row captures the product returned, the shopper and their demographic profile, the store where the return was processed, the date and time of the return, the reason for the return, and a full set of financial measures including the return amount, tax on the return, any processing fee, delivery cost for the return, cash refunded, charges reversed, store credit issued, and net loss to the business. This table supports analysis of return rates, return reasons, refund methods (cash, charge reversal, store credit), and the financial cost of returns by product, store, customer segment, or time period.

### store_refunds.address_ref

Short: The customer's address associated with this store return.

Long: Links the store refund to a specific address record in the addresses dimension. Used to analyze return rates and refund amounts by customer geography, including city, state, and ZIP code.

Sample values: No non-null sample values available.

### store_refunds.client_profile_ref

Short: The demographic profile of the returning customer.

Long: Links the store refund to a specific demographic segment record in the client_profiles dimension, capturing attributes such as gender, marital status, and education at the time of the return. Used for demographic segmentation of return behavior.

Sample values: No non-null sample values available.

### store_refunds.client_ref

Short: The customer who returned the item at the store.

Long: Links the store refund to a specific shopper account in the clients dimension. Used to analyze return behavior by individual customers, identify high-return shoppers, and study return patterns relative to purchase history.

Sample values: No non-null sample values available.

### store_refunds.fee

Short: A processing or restocking fee charged on this return.

Long: A fee charged to the customer in connection with the return, such as a restocking or handling fee. Values range from $0.50 to $100.00. Used to analyze fee revenue from returns and the net cost of the return to the business.

Sample values: No non-null sample values available.

### store_refunds.household_profile_ref

Short: The household profile of the returning customer.

Long: Links the store refund to a household-level segment record in the household_profiles dimension, capturing attributes such as income range and buying potential. Used for household-level segmentation of return behavior.

Sample values: No non-null sample values available.

### store_refunds.merchandise_ref

Short: The product that was returned in this store refund.

Long: Links each store refund line to a specific product record in the merchandise dimension. Used to identify which items are returned most frequently, and to join product attributes such as brand, category, and class for return rate analysis.

Sample values: No non-null sample values available.

### store_refunds.net_loss

Short: The net financial loss to the business from this store return.

Long: The total financial impact of the return on the business, representing the net loss incurred after accounting for the refund amount, fees, and other return-related costs. Always a positive value in this table, indicating a loss. Used to measure the true cost of returns by product, store, customer segment, or return reason.

Sample values: No non-null sample values available.

### store_refunds.refunded_cash

Short: The amount refunded to the customer as cash.

Long: The portion of the refund paid back to the customer in cash. One of three refund methods alongside reversed charges and store credit. Used to analyze the mix of refund payment methods and total cash outflow from returns.

Sample values: No non-null sample values available.

### store_refunds.retail_location_ref

Short: The physical store where the return was processed.

Long: Links the store refund to a specific retail location in the retail_locations dimension. Only six distinct store locations appear in the data. Used to compare return volumes, refund amounts, and return reasons across store locations.

Sample values: No non-null sample values available.

### store_refunds.return_amount

Short: The refund amount for this return line, excluding tax.

Long: The monetary value of the merchandise returned, before tax. Represents the base refund amount owed to the customer for the returned items. Used to measure the financial value of returns and compare against original sales amounts.

Sample values: No non-null sample values available.

### store_refunds.return_amount_with_tax

Short: Total refund amount including tax for this return line.

Long: The total monetary value refunded to the customer for this return line, including applicable sales tax. Used when tax-inclusive refund figures are needed for financial reporting or reconciliation.

Sample values: No non-null sample values available.

### store_refunds.return_calendar_day_ref

Short: The calendar date when the in-store return was processed.

Long: Links each store refund to a specific calendar day in the calendar_days dimension. Used to analyze return trends over time, including daily, weekly, monthly, and seasonal return patterns at physical store locations.

Sample values: No non-null sample values available.

### store_refunds.return_clock_time_ref

Short: The time of day when the in-store return was processed.

Long: Links each store refund to a specific time of day in the clock_times dimension. Enables analysis of return activity by hour or shift, supporting operational questions about when returns are most frequently processed in stores.

Sample values: No non-null sample values available.

### store_refunds.return_delivery_cost

Short: The shipping or delivery cost associated with this return.

Long: The cost of delivering or shipping the returned item, which may be borne by the business or the customer depending on policy. Used to calculate the total cost of processing returns including logistics expenses.

Sample values: No non-null sample values available.

### store_refunds.return_quantity

Short: The number of units returned.

Long: The quantity of the merchandise item returned in this refund line. Values range from 1 to 100. Used to calculate total units returned, return rates relative to units sold, and volume-based return metrics.

Sample values: No non-null sample values available.

### store_refunds.return_reason_ref

Short: The reason the customer returned the item.

Long: Links the store refund to a specific return reason record in the return_reasons dimension. Up to 35 distinct return reasons are available. Used to analyze why customers return items, supporting product quality, customer satisfaction, and operational improvement analysis.

Sample values: No non-null sample values available.

### store_refunds.return_tax

Short: The tax amount refunded on this return.

Long: The tax portion of the refund for this return line. Many returns carry zero tax, reflecting tax-exempt items or locations. Used to calculate the total tax-inclusive refund amount and to analyze tax refund liability.

Sample values: No non-null sample values available.

### store_refunds.reversed_charge

Short: The amount refunded by reversing a credit or charge payment.

Long: The portion of the refund returned to the customer by reversing a prior charge, such as a credit card reversal. One of three refund methods alongside cash and store credit. Used to analyze refund method mix and charge-back activity.

Sample values: No non-null sample values available.

### store_refunds.store_credit

Short: The amount refunded to the customer as store credit.

Long: The portion of the refund issued to the customer as store credit rather than cash or a charge reversal. One of three refund methods. Used to analyze how much refund value is retained within the business as future purchase potential versus paid out directly.

Sample values: No non-null sample values available.

### store_refunds.ticket_number

Short: The sales receipt or transaction identifier associated with this return.

Long: A numeric identifier linking the return to the original sales ticket or receipt. Can be used to match refund records back to the original store purchase transaction. Multiple return lines may share a ticket number if multiple items from the same original transaction were returned.

Sample values: No non-null sample values available.

## support_centers

The support_centers table contains one record per customer service or support center location used by the business to assist shoppers, particularly in mail-order and assisted commerce operations. Each record describes a support center's physical address, size in square feet, number of employees, operating hours, and the manager responsible. Centers are classified by size (e.g., large, medium) and are associated with a market, division, and company for organizational reporting. The table supports slowly changing dimension tracking through record effective start and end dates, allowing historical analysis of center attributes over time. Geographic fields such as city, state, ZIP, county, and country enable location-based analysis. A tax percentage is stored at the center level, and a timezone offset supports time-of-day comparisons across regions. With only 6 rows, this is a small reference dimension. The closure and opening calendar day references link to the calendar_days table to capture when centers opened or closed.

### support_centers.city

Short: City where the support center is located.

Long: The city in which the support center operates. Observed values are 'Midway' and 'Fairview', reflecting the small number of distinct locations in the dataset. This field supports geographic filtering and regional analysis of support center activity.

Sample values: No non-null sample values available.

### support_centers.class

Short: Size classification of the support center, such as large or medium.

Long: A categorical label describing the relative size or tier of the support center. Observed values are 'large' and 'medium', with large centers being more common in the sample. This classification may be used to segment support center capacity, staffing expectations, or operational benchmarks.

Sample values: No non-null sample values available.

### support_centers.closure_calendar_day_ref

Short: Reference to the calendar day when the support center closed. Currently null for all centers, suggesting none have closed.

Long: A reference to the calendar_days table indicating the date on which a support center permanently closed. In the current data, this field is null for all six records, suggesting that all support centers in the dataset remain open. This field would be populated if a center were shut down, enabling closure-date analysis.

Sample values: No non-null sample values available.

### support_centers.company

Short: Numeric code identifying the company or legal entity the support center belongs to.

Long: A numeric identifier for the company or corporate entity that owns or operates the support center. Four distinct company codes are observed. This code corresponds to the company_name field and supports filtering or aggregation by corporate entity in multi-company reporting scenarios.

Sample values: No non-null sample values available.

### support_centers.company_name

Short: Name of the company or corporate entity the support center belongs to.

Long: The name of the company associated with the support center. Sample values such as 'pri', 'able', 'cally', and 'ought' appear to be synthetic or abbreviated placeholders, so the precise business meaning is uncertain. In a production context, this field would contain the actual company or brand name for organizational reporting.

Sample values: No non-null sample values available.

### support_centers.country

Short: Country where the support center is located.

Long: The country in which the support center operates. All records in the sample show 'United States', indicating the current dataset covers domestic support centers only. This field would support international filtering if non-US centers were added in the future.

Sample values: No non-null sample values available.

### support_centers.county

Short: County where the support center is located.

Long: The county in which the support center is situated. All six records in the sample show 'Williamson County', suggesting the current dataset covers a geographically concentrated set of support centers. This field supports county-level geographic analysis.

Sample values: No non-null sample values available.

### support_centers.division

Short: Numeric code identifying the organizational division the support center belongs to.

Long: A numeric identifier for the business division that the support center is part of. Four distinct division codes are observed across the six centers. This code corresponds to the division_name field and can be used to group support centers by organizational unit for reporting or filtering.

Sample values: No non-null sample values available.

### support_centers.division_name

Short: Name of the organizational division the support center belongs to.

Long: The name of the business division associated with the support center. Sample values such as 'anti', 'ese', 'ought', and 'pri' appear to be synthetic or abbreviated placeholders, so the precise business meaning is uncertain. In a production context, this field would contain meaningful division names for organizational grouping and reporting.

Sample values: No non-null sample values available.

### support_centers.employees

Short: Number of employees working at the support center.

Long: The headcount of staff assigned to the support center. Values in the sample range from 1 to 7, consistent with the small number of locations. This field can be used to analyze staffing levels relative to transaction volume, center size, or market area.

Sample values: No non-null sample values available.

### support_centers.hours

Short: Operating hours of the support center, such as 8AM–4PM.

Long: The scheduled operating hours for the support center. Observed values include '8AM-4PM' and '8AM-8AM', where the latter may indicate a 24-hour or extended-shift operation. This field is useful for understanding when assisted commerce support is available and for correlating support activity with time-of-day transaction patterns.

Sample values: No non-null sample values available.

### support_centers.manager

Short: Name of the manager responsible for the support center.

Long: The full name of the individual managing the support center. Sample values include names such as 'Larry Mccray' and 'Bob Belcher'. This field can be used to attribute center performance to a specific manager or to filter records by management responsibility.

Sample values: No non-null sample values available.

### support_centers.market_class

Short: Descriptive classification of the support center's market area.

Long: A text description classifying the market associated with the support center. The sample values appear to be synthetic or placeholder text, so the precise business meaning is uncertain. In a production context, this field likely describes the market tier, competitive environment, or demographic profile of the market area.

Sample values: No non-null sample values available.

### support_centers.market_code

Short: Numeric code identifying the market area associated with the support center.

Long: A numeric identifier for the market or geographic territory in which the support center operates. Three distinct market codes are observed across the six centers. This code links to market-level attributes such as market_class, market_description, and market_manager stored in the same record.

Sample values: No non-null sample values available.

### support_centers.market_description

Short: Longer description of the market area served by the support center.

Long: A free-text description providing additional context about the market in which the support center operates. Sample values appear to be synthetic placeholder text, so the precise business meaning is uncertain. In a production context, this field likely provides narrative detail about the market's characteristics or strategic importance.

Sample values: No non-null sample values available.

### support_centers.market_manager

Short: Name of the manager responsible for the support center's market area.

Long: The full name of the individual who manages the broader market territory in which the support center is located. Sample values include names such as 'Gary Colburn' and 'Julius Durham'. This is distinct from the center-level manager and represents a higher level of organizational responsibility.

Sample values: No non-null sample values available.

### support_centers.name

Short: The name of the support center, often reflecting its regional market area.

Long: The display name of the support center location. Sample values such as 'Mid Atlantic', 'NY Metro', and 'North Midwest' suggest that center names reflect the geographic market or region they serve. This field is useful for filtering or grouping support center activity by named region.

Sample values: No non-null sample values available.

### support_centers.opening_calendar_day_ref

Short: Reference to the calendar day when the support center opened for business.

Long: A reference to the calendar_days table indicating the date on which a support center first opened. This allows analysts to calculate center tenure, compare performance by opening cohort, or filter to centers that were open during a specific period. Three distinct opening dates are observed across the six centers.

Sample values: No non-null sample values available.

### support_centers.record_effective_end

Short: Date when this version of the support center record expired. Null indicates the record is currently active.

Long: The end date of the period during which this support center record's attributes were valid. A null value indicates the record is the current active version. When populated, it marks the day before a new version of the record took effect. Approximately half of the records in the sample have a null end date, suggesting those are the current active records.

Sample values: No non-null sample values available.

### support_centers.record_effective_start

Short: Date when this version of the support center record became active.

Long: The start date of the period during which this support center record's attributes are considered current and valid. Used in combination with record_effective_end to support slowly changing dimension (SCD) tracking, allowing analysts to look up the correct center attributes as of any historical date. Values observed range from 1998 to 2002.

Sample values: No non-null sample values available.

### support_centers.square_ft

Short: Physical size of the support center in square feet.

Long: The floor area of the support center measured in square feet. Values range from 649 to 4,134 square feet across the six centers, reflecting meaningful variation in facility size. This can be used alongside employee counts and transaction volumes to assess operational efficiency or capacity utilization.

Sample values: No non-null sample values available.

### support_centers.state

Short: US state where the support center is located.

Long: The two-letter US state abbreviation for the support center's location. All records in the sample show 'TN' (Tennessee), consistent with the county and city data. This field supports state-level geographic filtering and regional reporting.

Sample values: No non-null sample values available.

### support_centers.street_name

Short: Street name component of the support center's physical address.

Long: The name of the street where the support center is located. Sample values include '14th', 'Ash Hill', and 'Franklin'. Combined with the other address fields, this identifies the physical location of the support center.

Sample values: No non-null sample values available.

### support_centers.street_number

Short: Street number component of the support center's physical address.

Long: The numeric portion of the support center's street address. Combined with street_name, street_type, suite_number, city, state, and ZIP, this field forms the complete mailing address of the support center location.

Sample values: No non-null sample values available.

### support_centers.street_type

Short: Street type suffix for the support center's address, such as Boulevard or Court.

Long: The street type or suffix that follows the street name in the support center's address. Observed values include 'Boulevard', 'Court', and 'Wy' (Way). This field completes the street address line when combined with street_number and street_name.

Sample values: No non-null sample values available.

### support_centers.suite_number

Short: Suite or unit number within the support center's building address.

Long: The suite or unit designation for the support center's location within a building. Sample values include 'Suite 0', 'Suite 60', and 'Suite 150'. This field is part of the full mailing address and may be relevant for mail delivery or facility identification.

Sample values: No non-null sample values available.

### support_centers.support_center_code

Short: Business-facing code that identifies a support center, used to link support centers to purchase and refund records.

Long: The business-facing identifier for a support center location. This code is used to join the support_centers dimension to transaction tables such as mail_order_purchases and mail_order_refunds, where it appears as support_center_ref. Because the table tracks historical versions of center attributes, multiple records may share the same support_center_code across different effective date ranges.

Sample values: No non-null sample values available.

### support_centers.support_center_record

Short: Internal row identifier for each support center record. Appears to be a system-generated surrogate key.

Long: A numeric identifier that uniquely identifies each row in the support_centers table. With only 6 distinct values matching the total row count, this appears to be a system-generated internal key used for row-level identification rather than a business-facing reference. Business users should use support_center_code for external identification.

Sample values: No non-null sample values available.

### support_centers.tax_percentage

Short: Local tax rate applicable at the support center's location, expressed as a decimal.

Long: The tax rate associated with the support center's jurisdiction, stored as a decimal value (e.g., 0.11 represents 11%). Values range from 0.01 to 0.12 across the six centers. This rate may be applied to transactions processed through or associated with the support center for tax calculation purposes.

Sample values: No non-null sample values available.

### support_centers.timezone_offset

Short: UTC timezone offset for the support center's location, used for time-of-day analysis.

Long: The offset from Coordinated Universal Time (UTC) for the support center's geographic location. All records in the sample show -5.00, consistent with Eastern Standard Time (UTC-5). This field is useful for normalizing transaction timestamps across time zones when analyzing support center activity relative to purchase or return events.

Sample values: No non-null sample values available.

### support_centers.zip

Short: Postal ZIP code for the support center's location.

Long: The five-digit US postal ZIP code for the support center's address. Two distinct ZIP codes are observed across the six centers. This field supports geographic analysis at the ZIP code level and can be used to map support center locations.

Sample values: No non-null sample values available.
